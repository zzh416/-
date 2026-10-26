import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import Asset, User

client = TestClient(app)


def collection() -> str:
    response = client.post('/api/v1/collections', json={
        'name': f'批量验收-{uuid4()}', 'purpose': 'INTERVIEW_EXPERIENCE',
    })
    assert response.status_code == 201
    return response.json()['id']


def create_batch(collection_id, urls, key=None):
    return client.post(f'/api/v1/collections/{collection_id}/url-imports',
                       json={'urls': urls}, headers={'Idempotency-Key': key or str(uuid4())})


def test_mixed_batch_is_persisted_deduplicated_and_idempotent():
    cid = collection()
    key = str(uuid4())
    urls = [' https://EXAMPLE.com/a ', 'https://example.com/a', 'not a link',
            'http://127.0.0.1/private', 'https://example.com/b']
    try:
        with patch('app.workers.celery_app.celery_app.send_task', return_value=SimpleNamespace(id='task')) as send:
            response = create_batch(cid, urls, key)
            assert response.status_code == 201
            batch = response.json()
            assert [i['disposition'] for i in batch['items']] == ['CREATED', 'DUPLICATE', 'INVALID', 'INVALID', 'CREATED']
            assert batch['items'][1]['asset']['id'] == batch['items'][0]['asset']['id']
            assert batch['items'][1]['duplicateOf'] == 1
            assert send.call_count == 2
            replay = create_batch(cid, urls, key)
            assert replay.json()['id'] == batch['id']
            assert send.call_count == 2
            assert create_batch(cid, urls + ['https://example.com/c'], key).status_code == 409
            reused = create_batch(cid, ['https://example.com/a'])
            assert reused.json()['items'][0]['disposition'] == 'REUSED'
            assert send.call_count == 2
        fetched = client.get(f"/api/v1/url-imports/{batch['id']}")
        assert fetched.json()['items'] == batch['items']
        assert len(client.get(f'/api/v1/collections/{cid}/url-imports').json()['items']) == 2
        assert len(client.get(f'/api/v1/collections/{cid}/assets').json()['items']) == 2
    finally:
        client.delete(f'/api/v1/collections/{cid}')


def test_batch_preserves_chapters_query_and_fragment_and_reuse_is_collection_scoped():
    cid, other = collection(), collection()
    urls = ['https://example.com/book#/one', 'https://example.com/book#/two',
            'https://example.com/book?q=1', 'https://example.com/book?q=2',
            'https://example.com/book/', 'https://example.com/book']
    try:
        with patch('app.workers.celery_app.celery_app.send_task', return_value=SimpleNamespace(id='task')) as send:
            batch = create_batch(cid, urls).json()
            assert [i['normalizedUrl'] for i in batch['items']] == urls
            assert all(i['disposition'] == 'CREATED' for i in batch['items'])
            next_batch = create_batch(other, [urls[0]]).json()
            assert next_batch['items'][0]['disposition'] == 'CREATED'
            assert send.call_count == 7
    finally:
        client.delete(f'/api/v1/collections/{cid}')
        client.delete(f'/api/v1/collections/{other}')


def test_queue_failure_is_per_item_and_current_state_and_deletion_are_visible():
    cid = collection()
    try:
        with patch('app.workers.celery_app.celery_app.send_task', side_effect=[
            SimpleNamespace(id='first'), RuntimeError('queue down'), SimpleNamespace(id='third'),
        ]):
            response = create_batch(cid, [f'https://example.com/{i}' for i in range(3)])
        assert response.status_code == 201
        batch = response.json()
        assert [i['asset']['status'] for i in batch['items']] == ['QUEUED', 'FAILED', 'QUEUED']
        failed_id = batch['items'][1]['asset']['id']
        with patch('app.workers.celery_app.celery_app.send_task') as send:
            assert create_batch(cid, ['https://example.com/1']).json()['items'][0]['asset']['status'] == 'FAILED'
            send.assert_not_called()
        with patch('app.workers.celery_app.celery_app.send_task', return_value=SimpleNamespace(id='retry')):
            assert client.post(f'/api/v1/assets/{failed_id}/retry').json()['job']['retryCount'] == 1
        assert client.get(f"/api/v1/url-imports/{batch['id']}").json()['items'][1]['asset']['job']['retryCount'] == 1
        assert client.delete(f'/api/v1/assets/{failed_id}').status_code == 204
        assert client.get(f"/api/v1/url-imports/{batch['id']}").json()['items'][1]['asset'] is None
    finally:
        client.delete(f'/api/v1/collections/{cid}')


def test_batch_bounds_and_ownership():
    cid = collection()
    owner = uuid4()
    try:
        assert create_batch(cid, []).status_code == 422
        assert create_batch(cid, ['https://example.com'] * 51).status_code == 422
        assert client.post(f'/api/v1/collections/{cid}/url-imports', json={'urls': ['https://example.com']}).status_code == 422
        with patch('app.workers.celery_app.celery_app.send_task'):
            batch = create_batch(cid, ['https://example.com']).json()
        with SessionLocal() as db:
            db.add(User(id=owner, display_name='Other batch owner'))
            db.commit()
        app.dependency_overrides[get_current_user_id] = lambda: owner
        assert create_batch(cid, ['https://example.com']).status_code == 404
        assert client.get(f'/api/v1/collections/{cid}/url-imports').status_code == 404
        assert client.get(f"/api/v1/url-imports/{batch['id']}").status_code == 404
        with SessionLocal() as db:
            assert db.scalar(select(Asset).where(Asset.owner_id == owner)) is None
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
        client.delete(f'/api/v1/collections/{cid}')
        with SessionLocal() as db:
            user = db.get(User, owner)
            if user:
                db.delete(user)
                db.commit()


def test_committed_batch_recovers_unpublished_jobs_without_republishing_others():
    cid, key = collection(), str(uuid4())
    urls = ['https://example.com/recover-a', 'https://example.com/recover-b']
    try:
        with patch('app.routes.url_imports._dispatch_pending', side_effect=RuntimeError('process stopped')):
            with pytest.raises(RuntimeError, match='process stopped'):
                create_batch(cid, urls, key)
        batch = client.get(f'/api/v1/collections/{cid}/url-imports').json()['items'][0]
        assert all(i['asset']['job']['celeryTaskId'] is None for i in batch['items'])
        with patch('app.workers.celery_app.celery_app.send_task', return_value=SimpleNamespace(id='recovered')) as send:
            response = create_batch(cid, urls, key)
            assert response.json()['id'] == batch['id']
            assert send.call_count == 2
            assert client.post(f"/api/v1/url-imports/{batch['id']}/dispatch").status_code == 200
            assert send.call_count == 2
    finally:
        client.delete(f'/api/v1/collections/{cid}')


def test_concurrent_batches_in_one_collection_create_only_one_asset():
    cid = collection()
    try:
        with patch('app.workers.celery_app.celery_app.send_task', return_value=SimpleNamespace(id='concurrent')) as send:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: create_batch(cid, ['https://example.com/concurrent']), range(2)))
            assert all(response.status_code == 201 for response in responses)
            assert sorted(response.json()['items'][0]['disposition'] for response in responses) == ['CREATED', 'REUSED']
            assert send.call_count == 1
        assert len(client.get(f'/api/v1/collections/{cid}/assets').json()['items']) == 1
    finally:
        client.delete(f'/api/v1/collections/{cid}')


def test_long_urls_and_hostname_cannot_break_other_lines():
    cid = collection()
    try:
        with patch('app.workers.celery_app.celery_app.send_task', return_value=SimpleNamespace(id='long')):
            response = create_batch(cid, ['https://' + 'a' * 250 + '.com/', 'https://example.com/' + 'a' * 2050,
                                          'https://example.com/good'])
        assert response.status_code == 201
        assert response.json()['items'][1]['disposition'] == 'INVALID'
        assert response.json()['items'][2]['disposition'] == 'CREATED'
    finally:
        client.delete(f'/api/v1/collections/{cid}')


@pytest.mark.parametrize('bad, expected', [('https://example.com/\x00', 201), ('https://example.com/\ud800', 422)])
def test_unstorable_characters_are_handled_without_server_errors(bad, expected):
    cid = collection()
    try:
        with patch('app.workers.celery_app.celery_app.send_task', return_value=SimpleNamespace(id='unicode')):
            response = client.post(f'/api/v1/collections/{cid}/url-imports',
                content=json.dumps({'urls': [bad, 'https://example.com/good']}),
                headers={'Content-Type': 'application/json', 'Idempotency-Key': str(uuid4())})
        assert response.status_code == expected
        if expected == 201:
            assert response.json()['items'][0]['disposition'] == 'INVALID'
            assert response.json()['items'][1]['disposition'] == 'CREATED'
        else:
            assert all('input' not in error for error in response.json()['detail'])
    finally:
        client.delete(f'/api/v1/collections/{cid}')


def test_new_batch_can_recover_reused_unpublished_asset():
    cid = collection()
    try:
        with patch('app.routes.url_imports._dispatch_pending', side_effect=RuntimeError('process stopped')):
            with pytest.raises(RuntimeError):
                create_batch(cid, ['https://example.com/reused-pending'])
        with patch('app.workers.celery_app.celery_app.send_task', return_value=SimpleNamespace(id='recovered-reuse')) as send:
            response = create_batch(cid, ['https://example.com/reused-pending'] * 2)
            assert response.status_code == 201
            assert response.json()['items'][0]['disposition'] == 'REUSED'
            assert response.json()['items'][0]['asset']['job']['celeryTaskId'] == 'recovered-reuse'
            assert send.call_count == 1
    finally:
        client.delete(f'/api/v1/collections/{cid}')


def test_delete_collection_cannot_remove_a_batch_being_committed():
    from app.modules.ingestion_validation import normalize_public_url
    cid = collection()
    entered, release = Event(), Event()

    def pause_normalization(url):
        entered.set()
        assert release.wait(timeout=5)
        return normalize_public_url(url)

    try:
        with patch('app.routes.url_imports.normalize_public_url', side_effect=pause_normalization), \
             patch('app.workers.celery_app.celery_app.send_task', return_value=SimpleNamespace(id='delete-race')):
            with ThreadPoolExecutor(max_workers=2) as pool:
                imported = pool.submit(create_batch, cid, ['https://example.com/keep'])
                assert entered.wait(timeout=5)
                deleted = pool.submit(client.delete, f'/api/v1/collections/{cid}')
                try:
                    with pytest.raises(TimeoutError):
                        deleted.result(timeout=0.2)
                finally:
                    release.set()
                assert imported.result().status_code == 201
                assert deleted.result().status_code == 409
    finally:
        release.set()
        client.delete(f'/api/v1/collections/{cid}')


def test_concurrent_same_key_replays_same_batch_and_conflicting_input_is_rejected():
    cid, key = collection(), str(uuid4())
    try:
        with patch('app.workers.celery_app.celery_app.send_task', return_value=SimpleNamespace(id='same-key')) as send:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: create_batch(cid, ['https://example.com/once'], key), range(2)))
            assert all(response.status_code == 201 for response in responses)
            assert responses[0].json()['id'] == responses[1].json()['id']
            assert send.call_count == 1
            with ThreadPoolExecutor(max_workers=2) as pool:
                conflicts = list(pool.map(lambda i: create_batch(cid, [f'https://example.com/{i}'], key), range(2)))
            assert all(response.status_code == 409 for response in conflicts)
    finally:
        client.delete(f'/api/v1/collections/{cid}')

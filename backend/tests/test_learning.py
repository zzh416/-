from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import InterviewSession, Question, User

client = TestClient(app)


@pytest.fixture
def learning(monkeypatch):
    owner = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner, display_name='学习状态验收'))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: owner
    monkeypatch.setattr('app.workers.celery_app.celery_app.send_task', lambda *a, **k: None)
    questions = [client.post('/api/v1/questions', json={'prompt': p, 'answer': a}).json() for p, a in [
        ('Python 生成器是什么？', '通过 yield 惰性产生值'), ('MySQL 索引是什么？', 'B+树'),
        ('RAG 如何实现？', '可以用 Java 实现')]]
    yield owner, questions
    app.dependency_overrides.pop(get_current_user_id, None)
    with SessionLocal() as db:
        db.delete(db.get(User, owner))
        db.commit()


def lookup(question):
    response = client.post('/api/v1/learning/lookup', json={'questionIds': [question['id']]})
    assert response.status_code == 200
    return response.json()['items'][0]


def mark(question, status, state=None):
    state = state or lookup(question)
    return client.put(f"/api/v1/learning/questions/{question['id']}", json={
        'status': status, 'expectedVersion': state['version'],
        'questionVersion': state['questionVersion'], 'answerVersion': state['answerVersion']})


def test_manual_state_and_priority_queue_preserve_recommendation(learning):
    _, questions = learning
    run = client.post('/api/v1/recommendation-runs', json={}).json()
    assert lookup(questions[0])['status'] == 'UNMARKED'
    assert mark(questions[0], 'WEAK').status_code == 200
    assert mark(questions[1], 'UNKNOWN').status_code == 200
    assert mark(questions[2], 'MASTERED').status_code == 200
    queue = client.get('/api/v1/learning/items').json()
    assert [i['questionId'] for i in queue['items']] == [questions[1]['id'], questions[0]['id']]
    assert queue['unknownCount'] == 1 and queue['masteredCount'] == 1
    assert client.get('/api/v1/learning/items', params={'q': '生成器'}).json()['total'] == 1
    assert client.get('/api/v1/learning/items', params={'state': 'ANY', 'pageSize': 1, 'page': 2}).json()['total'] == 3
    current = client.get(f"/api/v1/recommendation-runs/{run['id']}").json()
    assert current['isCurrent'] and current['includedCount'] == run['includedCount']
    mark(questions[1], 'MASTERED')
    assert client.get('/api/v1/learning/items').json()['total'] == 1


def test_queue_uses_current_exclusions_and_answer_visibility(learning):
    _, questions = learning
    for question in questions:
        mark(question, 'UNKNOWN')
    client.post('/api/v1/preference-rules', json={'ruleType': 'EXCLUDE', 'technology': 'MySQL', 'scope': 'BOTH'})
    client.post('/api/v1/preference-rules', json={'ruleType': 'EXCLUDE', 'technology': 'Java', 'scope': 'ANSWER'})
    queue = client.get('/api/v1/learning/items').json()
    assert queue['excludedCount'] == 1 and queue['total'] == 2
    rag = next(i for i in queue['items'] if i['questionId'] == questions[2]['id'])
    assert rag['answerContent'] is None
    assert lookup(questions[1])['status'] == 'UNKNOWN'


def test_state_conflicts_and_changed_content_require_reconfirmation(learning):
    _, questions = learning
    first = lookup(questions[0])
    saved = mark(questions[0], 'MASTERED', first)
    assert saved.status_code == 200
    assert mark(questions[0], 'UNKNOWN', first).status_code == 409
    client.put(f"/api/v1/questions/{questions[0]['id']}", json={'prompt': 'Python 生成器与迭代器区别？', 'expectedVersion': 1})
    assert mark(questions[0], 'UNKNOWN', saved.json()).status_code == 409
    queue = client.get('/api/v1/learning/items').json()
    assert queue['total'] == 1 and queue['items'][0]['contentChanged']
    assert mark(questions[0], 'MASTERED').status_code == 200
    assert client.get('/api/v1/learning/items').json()['total'] == 0
    client.delete(f"/api/v1/questions/{questions[0]['id']}")
    assert client.get('/api/v1/learning/items', params={'state': 'ANY'}).json()['total'] == 0


def test_owner_scope_and_concurrent_first_update(learning):
    owner, questions = learning
    first = lookup(questions[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda state: mark(questions[0], state, first), ['WEAK', 'UNKNOWN']))
    assert sorted(r.status_code for r in results) == [200, 409]
    record = lookup(questions[0])
    other = uuid4()
    with SessionLocal() as db:
        db.add(User(id=other, display_name='Other learning owner'))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: other
    try:
        assert client.post('/api/v1/learning/lookup', json={'questionIds': [questions[0]['id']]}).status_code == 404
        assert mark(questions[0], 'UNKNOWN', record).status_code == 404
        assert client.put(f"/api/v1/learning/items/{record['id']}", json={'status': 'MASTERED', 'expectedVersion': record['version'], 'questionVersion': record['questionVersion'], 'answerVersion': record['answerVersion']}).status_code == 404
        assert client.get('/api/v1/learning/items').json()['total'] == 0
    finally:
        app.dependency_overrides[get_current_user_id] = lambda: owner
        with SessionLocal() as db:
            db.delete(db.get(User, other))
            db.commit()


def interview(learning):
    run = client.post('/api/v1/recommendation-runs', json={}).json()
    session = client.post('/api/v1/interview-sessions', json={'recommendationRunId': run['id'], 'questionCount': 3}).json()
    def action(session, value):
        return client.post(f"/api/v1/interview-sessions/{session['id']}/action", json={'action': value, 'expectedVersion': session['version']}).json()
    session = action(action(session, 'USE_BANK'), 'START')
    turn = session['turns'][-1]
    response = client.post(f"/api/v1/interview-sessions/{session['id']}/answer", json={
        'expectedVersion': session['version'], 'turnId': turn['id'], 'action': 'DONT_KNOW', 'text': ''})
    assert response.status_code == 200
    return response.json(), turn


def test_interview_unknown_requires_confirmation_and_replay_never_downgrades(learning):
    session, turn = interview(learning)
    assert client.get('/api/v1/learning/items').json()['total'] == 0
    path = f"/api/v1/learning/interviews/{session['id']}"
    candidates = client.get(path + '/candidates').json()['items']
    assert len(candidates) == 1 and not candidates[0]['confirmed']
    saved = client.post(path + f"/turns/{turn['id']}")
    assert saved.status_code == 200 and saved.json()['sourceKind'] == 'BANK'
    question = {'id': saved.json()['questionId']}
    assert mark(question, 'MASTERED').status_code == 200
    assert client.post(path + f"/turns/{turn['id']}").json()['status'] == 'MASTERED'
    assert client.get(path + '/candidates').json()['items'][0]['confirmed']
    assert client.post(path + f'/turns/{uuid4()}').status_code == 404


def test_ai_unknown_stays_separate_from_question_bank(learning):
    owner, _ = learning
    session, turn = interview(learning)
    with SessionLocal() as db:
        row = db.get(InterviewSession, UUID(session['id']))
        state = deepcopy(row.state)
        ai_turn = {**state['turns'][0], 'id': str(uuid4()), 'kind': 'AI_FOLLOWUP', 'depth': 1,
                   'prompt': 'Redis 缓存命中率如何测量？', 'answer_status': 'DONT_KNOW'}
        state['turns'].append(ai_turn)
        row.state = state
        db.commit()
        count = db.scalar(select(func.count(Question.id)).where(Question.owner_id == owner))
    saved = client.post(f"/api/v1/learning/interviews/{session['id']}/turns/{ai_turn['id']}")
    assert saved.status_code == 200
    assert saved.json()['sourceKind'] == 'INTERVIEW' and saved.json()['questionId'] is None
    queue = client.get('/api/v1/learning/items').json()
    assert queue['items'][0]['prompt'] == ai_turn['prompt'] and queue['items'][0]['answerContent'] is None
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Question.id)).where(Question.owner_id == owner)) == count


def test_answer_changes_reenter_queue_and_unmark_removes_record_from_review(learning):
    _, questions = learning
    question = questions[0]
    mark(question, 'MASTERED')
    changed = client.put(f"/api/v1/questions/{question['id']}/answer", json={'content': '通过 yield 逐次产生结果，并保存执行状态', 'expectedVersion': 1})
    assert changed.status_code == 200
    queue = client.get('/api/v1/learning/items').json()
    assert queue['changedCount'] == 1 and queue['items'][0]['contentChanged']
    assert mark(question, 'UNMARKED').status_code == 200
    assert client.get('/api/v1/learning/items', params={'state': 'ANY'}).json()['total'] == 0


def test_confirmation_is_concurrent_idempotent_and_does_not_change_interview(learning):
    session, turn = interview(learning)
    path = f"/api/v1/learning/interviews/{session['id']}/turns/{turn['id']}"
    before = client.get(f"/api/v1/interview-sessions/{session['id']}").json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert responses[0].json()['id'] == responses[1].json()['id']
    after = client.get(f"/api/v1/interview-sessions/{session['id']}").json()
    assert after['version'] == before['version'] and after['turns'] == before['turns']
    assert client.get('/api/v1/learning/items').json()['total'] == 1


def test_interview_candidates_cannot_accept_changed_bank_or_unanswered_turn(learning):
    owner, _ = learning
    session, turn = interview(learning)
    path = f"/api/v1/learning/interviews/{session['id']}"
    with SessionLocal() as db:
        question = db.scalar(select(Question).where(Question.owner_id == owner, Question.prompt == turn['prompt']))
        question.prompt += '（补充说明）'
        question.version += 1
        db.commit()
    candidate = client.get(path + '/candidates').json()['items'][0]
    assert not candidate['canConfirm'] and '更新' in candidate['message']
    assert client.post(path + f"/turns/{turn['id']}").status_code == 409
    current = client.get(f"/api/v1/interview-sessions/{session['id']}").json()
    unanswered = [t for t in current['turns'] if t['answerStatus'] == 'DRAFT']
    if unanswered:
        assert client.post(path + f"/turns/{unanswered[0]['id']}").status_code == 404

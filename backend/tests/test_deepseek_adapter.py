import json

import httpx
import pytest

from app.adapters.glm_profile import extract_profile_with_glm
from app.adapters.glm_text import ExtractionBlock, TextModelError, TextModelNotConfigured, extract_questions_with_glm


@pytest.mark.parametrize('extractor,source,payload', [
    (extract_questions_with_glm, [ExtractionBlock(0, '什么是缓存？')],
     {'items': [{'question': {'block_ordinal': 0, 'quote': '什么是缓存？'}, 'answer_spans': [], 'confidence': 1}]}),
    (extract_profile_with_glm, '熟悉 Python',
     {'target_role': None, 'target_role_evidence': None, 'skills': [{'name': 'Python', 'proficiency': 'INTERMEDIATE', 'evidence': '熟悉 Python'}]}),
])
def test_deepseek_uses_selected_credentials_and_json_contract(extractor, source, payload):
    def handler(request):
        assert str(request.url) == 'https://api.deepseek.com/chat/completions'
        assert request.headers['Authorization'] == 'Bearer private-test-key'
        body = json.loads(request.content)
        assert body['model'] == 'deepseek-chat'
        assert body['response_format'] == {'type': 'json_object'}
        assert 'JSON' in body['messages'][0]['content']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(payload)}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = extractor(source, api_key='private-test-key', base_url='https://api.deepseek.com',
                           model='deepseek-chat', provider='deepseek', client=client)
    assert result


@pytest.mark.parametrize('extractor,source,prefix', [
    (extract_questions_with_glm, [ExtractionBlock(0, '问题')], 'TEXT_MODEL'),
    (extract_profile_with_glm, 'Python', 'PROFILE_MODEL'),
])
@pytest.mark.parametrize('status,suffix,retryable,expected', [
    (401, 'AUTH_FAILED', False, 'DeepSeek'),
    (402, 'RESOURCE_EXHAUSTED', False, 'DeepSeek 账户余额不足'),
    (429, 'RATE_LIMITED', True, '限流'),
])
def test_deepseek_errors_are_actionable_and_do_not_leak_response(extractor, source, prefix, status, suffix, retryable, expected):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        status, json={'error': {'code': '1113', 'message': 'private-server-response'}}
    ))) as client:
        with pytest.raises(TextModelError) as error:
            extractor(source, api_key='private-test-key', base_url='https://api.deepseek.com',
                      model='deepseek-chat', provider='deepseek', client=client)
    assert error.value.code == f'{prefix}_{suffix}'
    assert error.value.retryable is retryable
    assert expected in str(error.value)
    assert 'private-' not in str(error.value)
    assert 'GLM' not in str(error.value)


@pytest.mark.parametrize('extractor,source', [
    (extract_questions_with_glm, [ExtractionBlock(0, '问题')]),
    (extract_profile_with_glm, 'Python'),
])
def test_deepseek_missing_key_names_correct_setting(extractor, source):
    with pytest.raises(TextModelNotConfigured, match='DEEPSEEK_API_KEY'):
        extractor(source, api_key='', base_url='https://api.deepseek.com',
                  model='deepseek-chat', provider='deepseek')

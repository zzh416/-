from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.adapters.interview_coach import InterviewPlan, InterviewReview
from app.adapters.text_provider import TextModelConfig
from app.db import SessionLocal
from app.identity import get_current_user_id
from app.main import app
from app.models import InterviewSession, Question, RecommendationRun, User
from app.modules.interview_service import process_interview

client = TestClient(app)


@pytest.fixture
def sources(monkeypatch):
    owner = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner, display_name='出题来源验收'))
        db.commit()
    app.dependency_overrides[get_current_user_id] = lambda: owner
    calls = []
    monkeypatch.setattr('app.workers.celery_app.celery_app.send_task', lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr('app.modules.interview_service.get_text_model_config', lambda: TextModelConfig('deepseek', 'synthetic', 'https://example.test', 'test'))
    profile = client.get('/api/v1/profiles/me').json()
    profile = client.post('/api/v1/profiles/me/skills', json={'displayName':'Python', 'proficiency':'INTERMEDIATE', 'expectedVersion':profile['version']}).json()
    assert profile['skills'][0]['reviewStatus'] == 'CONFIRMED'
    yield {'owner':owner, 'calls':calls, 'profile':profile}
    app.dependency_overrides.pop(get_current_user_id, None)
    with SessionLocal() as db:
        db.delete(db.get(User, owner))
        db.commit()


def create(source='AI', **kwargs):
    return client.post('/api/v1/interview-sessions', json={'questionSource':source,'questionCount':3,'mode':'PREVIEW',**kwargs})


def read(session):
    return client.get(f"/api/v1/interview-sessions/{session['id']}").json()


def action(session, name):
    return client.post(f"/api/v1/interview-sessions/{session['id']}/action", json={'action':name,'expectedVersion':session['version']})


def python_plan(payload, **kwargs):
    assert payload['question_source'] == 'AI' and payload['candidates'] == []
    return InterviewPlan(selected=[], new_questions=[
        {'question':'Python 生成器与普通函数有什么区别？','skill':'python','reason':'匹配已确认技能'},
        {'question':'Python 字典如何处理哈希冲突？','skill':'Python','reason':'基础结构'},
        {'question':'Python 上下文管理器适合哪些场景？','skill':'python','reason':'资源管理'},
    ])


def test_ai_works_with_only_confirmed_resume_skill_and_no_bank(sources):
    response = create()
    assert response.status_code == 202, response.text
    session = response.json()
    process_interview(UUID(session['id']), planner=python_plan)
    saved = read(session)
    assert saved['status'] == 'READY' and saved['questionSource'] == 'AI'
    assert len(saved['plan']) == 3 and all(i['kind'] == 'AI' and i['skillBasis'] == 'Python' for i in saved['plan'])
    assert action(saved, 'USE_BANK').status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Question.id)).where(Question.owner_id == sources['owner'])) == 0
        assert db.scalar(select(func.count(RecommendationRun.id)).where(RecommendationRun.owner_id == sources['owner'])) == 0


def test_bank_never_calls_planner_and_does_not_fill_with_ai(sources):
    for prompt in ['Python 生成器是什么？','Python GIL是什么？']:
        client.post('/api/v1/questions', json={'prompt':prompt})
    run = client.post('/api/v1/recommendation-runs', json={}).json()
    response = create('BANK', recommendationRunId=run['id'])
    assert response.status_code == 202
    saved = response.json()
    assert saved['status'] == 'READY' and len(saved['plan']) == 2 and saved['questionSource'] == 'BANK'
    assert all(i['kind'] == 'BANK' for i in saved['plan'])
    assert sources['calls'] == []


def test_ai_rejects_unconfirmed_or_unrelated_basis_and_excluded_questions(sources):
    client.post('/api/v1/preference-rules', json={'ruleType':'EXCLUDE','technology':'MySQL','scope':'BOTH'})
    session = create().json()
    def bad_and_good(payload, **kwargs):
        return InterviewPlan(selected=[], new_questions=[
            {'question':'Java JVM如何进行垃圾回收？','skill':'python','reason':'不应通过'},
            {'question':'Redis 缓存如何设置过期时间？','skill':'redis','reason':'未确认技能'},
            {'question':'Python 如何连接MySQL并创建索引？','skill':'python','reason':'排除MySQL'},
            {'question':'Redis 如何限制并发请求数量？','skill':'python','reason':'题干没有技能依据'},
            {'question':'Python 生成器与普通函数有什么区别？','skill':'python','reason':'有效题'},
            {'question':'Python 生成器与普通函数有什么区别？','skill':'python','reason':'重复题'},
        ])
    process_interview(UUID(session['id']), planner=bad_and_good)
    saved = read(session)
    assert saved['status'] == 'READY' and len(saved['plan']) == 1
    assert saved['plan'][0]['prompt'].startswith('Python 生成器')


def test_pure_ai_is_independent_of_question_bank_and_tracks_profile_rules(sources):
    session = create().json()
    process_interview(UUID(session['id']), planner=python_plan)
    assert read(session)['isCurrent']
    client.post('/api/v1/questions', json={'prompt':'Python 新增知识库题目？'})
    assert read(session)['isCurrent']
    client.post('/api/v1/preference-rules', json={'ruleType':'EXCLUDE','technology':'Python','scope':'QUESTION'})
    assert not read(session)['isCurrent']


def test_ai_question_can_receive_feedback_followup_and_unknown_confirmation(sources):
    session = create().json()
    process_interview(UUID(session['id']), planner=python_plan)
    session = action(read(session),'START').json()
    turn = session['turns'][-1]
    response = client.post(f"/api/v1/interview-sessions/{session['id']}/answer", json={'expectedVersion':session['version'],'turnId':turn['id'],'text':'Python 使用 yield 按需产生结果，并保存执行状态。'})
    assert response.status_code == 200
    process_interview(UUID(session['id']), reviewer=lambda *a, **k: InterviewReview(summary='可以进一步解释状态保留',covered_points=['按需产生'],gaps=[],change_topic=False,
        followup={'question':'Python 生成器暂停后如何保存执行状态？','answer_quote':'按需产生结果','question_quote':'Python 生成器'}))
    ready = read(session)
    assert ready['turns'][-1]['followup'] is not None
    following = action(ready,'FOLLOWUP').json()
    assert following['turns'][-1]['kind'] == 'AI_FOLLOWUP'
    turn_id = following['turns'][-1]['id']
    client.post(f"/api/v1/interview-sessions/{session['id']}/answer", json={'expectedVersion':following['version'],'turnId':turn_id,'action':'DONT_KNOW','text':''}).raise_for_status()
    learned = client.post(f"/api/v1/learning/interviews/{session['id']}/turns/{turn_id}")
    assert learned.status_code == 200 and learned.json()['sourceKind'] == 'INTERVIEW'


def test_ai_requires_confirmed_profile_and_other_sources_require_current_run(sources):
    profile = client.get('/api/v1/profiles/me').json()
    skill_id = profile['skills'][0]['id']
    client.delete(f'/api/v1/profiles/me/skills/{skill_id}',params={'expectedVersion':profile['version']}).raise_for_status()
    assert create().status_code == 409
    assert create('BANK').status_code == 422
    assert create('HYBRID').status_code == 422


def test_project_only_ai_honors_selected_project_and_literal_evidence(sources):
    profile = client.get('/api/v1/profiles/me').json()
    client.delete(f"/api/v1/profiles/me/skills/{profile['skills'][0]['id']}",params={'expectedVersion':profile['version']}).raise_for_status()
    profile = client.get('/api/v1/profiles/me').json()
    for name in ['RAG知识库甲', 'RAG知识库乙']:
        profile = client.post('/api/v1/profiles/me/projects',json={'name':name,'description':'使用 RAG 向量检索与重排处理内部文档','expectedVersion':profile['version']}).json()
    first, second = profile['projects']
    session = create(projectId=first['id']).json()
    def plan(payload, **kwargs):
        assert payload['confirmed_skills'] == [] and [p['id'] for p in payload['projects']] == [first['id']]
        return InterviewPlan(selected=[],new_questions=[
            {'project_id':first['id'],'project_quote':'RAG 向量检索与重排','question':'RAG 向量检索与重排如何组合？','reason':'项目依据'},
            {'project_id':second['id'],'project_quote':'RAG 向量检索与重排','question':'RAG 切块大小如何选择？','reason':'不在范围'},
            {'project_id':first['id'],'project_quote':'原文没有这段依据','question':'RAG 如何评估文档质量？','reason':'假引文'},
            {'skill':'python','question':'Python 内存如何管理？','reason':'未确认技能'},
        ])
    process_interview(UUID(session['id']),planner=plan)
    saved = read(session)
    assert saved['status'] == 'READY' and len(saved['plan']) == 1
    assert saved['plan'][0]['projectName'] == first['name']


def test_ai_failure_never_silently_falls_back_to_bank(sources):
    session = create().json()
    process_interview(UUID(session['id']),planner=lambda *a,**k: InterviewPlan(selected=[],new_questions=[]))
    failed = read(session)
    assert failed['status'] == 'PLAN_FAILED' and failed['plan'] == []
    assert action(failed,'USE_BANK').status_code == 409
    retry = action(failed,'RETRY_PLAN')
    assert retry.status_code == 200 and retry.json()['status'] == 'PLANNING'
    assert len(sources['calls']) == 2


def test_legacy_session_and_ai_profile_version_fences(sources):
    client.post('/api/v1/questions',json={'prompt':'Python 生成器如何运行？'})
    run = client.post('/api/v1/recommendation-runs',json={}).json()
    old = create('HYBRID',recommendationRunId=run['id']).json()
    with SessionLocal() as db:
        row = db.get(InterviewSession,UUID(old['id']))
        state = deepcopy(row.state)
        state.pop('question_source')
        state.pop('profile_version')
        state.pop('rules_fingerprint')
        row.state = state
        db.commit()
    assert read(old)['questionSource'] == 'HYBRID' and read(old)['isCurrent']
    action(read(old),'FINISH').raise_for_status()
    ai = create().json()
    profile = client.get('/api/v1/profiles/me').json()
    client.post('/api/v1/profiles/me/projects',json={'name':'Python项目','description':'用Python实现文本处理','expectedVersion':profile['version']}).raise_for_status()
    assert not read(ai)['isCurrent']
    process_interview(UUID(ai['id']),planner=python_plan)
    assert read(ai)['status'] == 'PLAN_FAILED'

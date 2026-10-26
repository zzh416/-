from uuid import uuid4
import json

import httpx

import pytest

from app.adapters.glm_text import TextModelError, TextModelNotConfigured
from app.adapters.interview_coach import InterviewPlan, generate_interview_plan
from app.adapters.text_provider import TextModelConfig
from app.modules.interview_service import _payload


def snapshot(count=8):
    project_id=str(uuid4())
    return {'question_source':'AI','question_count':count,'project_id':project_id,'candidates':[],
        'existing_prompt_hashes':[],'plan':[],
        'profile_snapshot':{'target_role':'合成验收','skills':[{'canonical_name':'python','display_name':'Python','category':'LANGUAGE','review_status':'CONFIRMED'}],
            'projects':[{'id':project_id,'name':'合成RAG项目','description':'使用Python实现RAG向量检索与重排，评估召回质量、耗时和缓存命中率。','review_status':'CONFIRMED'}]},
        'rule_snapshot':[{'rule_type':'EXCLUDE','canonical_technology':'java','technology':'Java','category':'LANGUAGE','scope':'BOTH'}]}


def batch(state, start, count):
    subjects=['切块大小','召回质量','重排选择','缓存命中率','检索扩展','流式响应','引用溯源','结果评估','资源预算','重复文档','请求超时','数据更新']
    return InterviewPlan(selected=[],new_questions=[{'project_id':state['project_id'],'project_quote':'RAG向量检索与重排',
        'question':f'RAG向量检索中如何设计{subjects[i]}？','reason':'依据项目已确认范围'} for i in range(start,start+count)])


def config():
    return TextModelConfig('deepseek','synthetic','https://example.test','test')


def test_project_scope_is_explicit_in_model_payload():
    state=snapshot()
    payload=_payload(state)
    assert payload['selected_project_id']==state['project_id']
    assert payload['allowed_basis']==['PROJECT']


def test_partial_valid_plan_is_preserved_and_missing_questions_are_filled():
    from app.modules.interview_planning import complete_plan
    state=snapshot()
    requests=[]
    def planner(payload,**kwargs):
        requests.append(payload)
        return batch(state,0,3) if len(requests)==1 else batch(state,3,5)
    plan,diag=complete_plan(state,planner,config())
    assert len(plan)==8 and diag['model_calls']==2
    assert requests[1]['question_count']==5
    assert requests[1]['desired_total']==8
    assert len(requests[1]['existing_questions'])==3
    assert len({p['prompt'] for p in plan})==8


def test_duplicate_outputs_stop_at_bounded_requests_and_explain_shortfall():
    from app.modules.interview_planning import complete_plan
    state=snapshot()
    calls=[]
    def planner(payload,**kwargs):
        calls.append(payload)
        return batch(state,0,3)
    plan,diag=complete_plan(state,planner,config())
    assert len(plan)==3 and len(calls)==3 and diag['exhausted']
    assert diag['rejection_counts']['DUPLICATE']>=6


def test_format_failure_is_retried_but_auth_failure_is_not():
    from app.modules.interview_planning import complete_plan
    state=snapshot()
    calls=[]
    def planner(payload,**kwargs):
        calls.append(payload)
        if len(calls)==1:
            raise TextModelError('INTERVIEW_INVALID_RESPONSE','格式错误',retryable=False)
        return batch(state,0,8)
    plan,diag=complete_plan(state,planner,config())
    assert len(plan)==8 and len(calls)==2 and diag['errors']==['INTERVIEW_INVALID_RESPONSE']
    calls.clear()
    def denied(payload,**kwargs):
        calls.append(payload)
        raise TextModelError('INTERVIEW_MODEL_AUTH_FAILED','无权限',retryable=False)
    with pytest.raises(TextModelError):
        complete_plan(state,denied,config())
    assert len(calls)==1


def test_one_malformed_item_does_not_discard_valid_questions(monkeypatch):
    state=snapshot(2)
    raw=batch(state,0,1).model_dump(mode='json')
    raw['new_questions'][0]['skill']=''
    raw['new_questions'].append({'project_id':'not-an-id','question':'RAG 如何评估检索效果？','reason':'无效ID','project_quote':'RAG向量检索与重排'})
    monkeypatch.setattr('app.adapters.interview_coach.request_json',lambda *a,**k:raw)
    result=generate_interview_plan(_payload(state),api_key='synthetic',base_url='https://example.test',model='test',provider='deepseek')
    assert len(result.new_questions)==1 and result.new_questions[0].skill is None
    assert result.validation_summary['invalid_items']==1


def test_refill_cannot_relax_scope_or_exclusions():
    from app.modules.interview_planning import complete_plan
    state=snapshot(3)
    def planner(payload,**kwargs):
        result=batch(state,0,1)
        result.new_questions.extend(InterviewPlan(selected=[],new_questions=[
            {'skill':'python','question':'Python 生成器如何执行？','reason':'范围外技能题'},
            {'project_id':state['project_id'],'project_quote':'RAG向量检索与重排','question':'Java 如何实现RAG向量检索？','reason':'排除技术'},
        ]).new_questions)
        return result
    plan,diag=complete_plan(state,planner,config())
    assert len(plan)==1
    assert diag['rejection_counts']['QUESTION_FILTER']>0
    assert diag['rejection_counts']['BASIS_SCOPE']>0


@pytest.mark.parametrize('content', [None, 42, 'not json'])
def test_invalid_transport_content_is_a_safe_format_error(content):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':content}}]}))) as client:
        with pytest.raises(TextModelError) as error:
            generate_interview_plan({},api_key='synthetic',base_url='https://example.test',model='test',provider='deepseek',client=client)
    assert error.value.code=='INTERVIEW_INVALID_RESPONSE'


def test_fenced_json_and_missing_empty_arrays_are_normalized_safely():
    state=snapshot()
    raw=batch(state,0,1).model_dump(mode='json')
    raw.pop('selected')
    raw['new_questions'][0]['skill']='  '
    content='```json\n'+json.dumps(raw)+'\n```'
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':content}}]}))) as client:
        plan=generate_interview_plan(_payload(state),api_key='synthetic',base_url='https://example.test',model='test',provider='deepseek',client=client)
    assert len(plan.new_questions)==1 and plan.selected==[] and plan.new_questions[0].skill is None


def test_schema_diagnostics_never_echo_model_values_or_unexpected_keys(monkeypatch):
    private='private-content-not-for-diagnostics'
    monkeypatch.setattr('app.adapters.interview_coach.request_json',lambda *a,**k:{'selected':[], 'new_questions':[{'question':'RAG 如何部署？','project_id':'secret-invalid-id','reason':private,private:'unexpected'}]})
    plan=generate_interview_plan({},api_key='synthetic',base_url='https://example.test',model='test',provider='deepseek')
    assert plan.validation_summary['invalid_items']==1
    assert private not in str(plan.validation_summary) and 'secret-invalid-id' not in str(plan.validation_summary)


def test_service_failure_during_refill_preserves_valid_questions():
    from app.modules.interview_planning import complete_plan
    state=snapshot()
    calls=[]
    def planner(payload,**kwargs):
        calls.append(payload)
        if len(calls)==1:
            return batch(state,0,3)
        raise TextModelNotConfigured('尚未配置')
    plan,diag=complete_plan(state,planner,config())
    assert len(plan)==3 and len(calls)==2
    assert diag['errors']==['INTERVIEW_MODEL_NOT_CONFIGURED'] and diag['exhausted']

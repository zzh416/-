from uuid import uuid4

from app.modules.project_matching import match_projects, prepare_projects


def project(name, description, status="CONFIRMED"):
    return {"id": str(uuid4()), "name": name, "description": description, "review_status": status}


def test_projects_match_multiple_explicit_topics_with_alias_boundaries():
    projects = [project("知识库", "Python RAG 向量检索"), project("聊天", "Python LangGraph 多智能体"), project("待确认", "RAG", "PENDING"), project("已排除", "RAG", "REJECTED")]
    matches = match_projects("RAG 如何与 Python 配合？", prepare_projects(projects))
    assert [item["project_name"] for item in matches] == ["知识库", "聊天"]
    assert matches[0]["matched_topics"] == ["Python", "RAG"]
    assert match_projects("storage 的原理是什么？", prepare_projects([project("Go后端", "Go")])) == []
    assert match_projects("ragged 字符串是什么？", prepare_projects(projects)) == []


def test_generic_question_and_empty_projects_remain_unmatched():
    assert match_projects("什么是进程和线程？", prepare_projects([project("知识库", "RAG")])) == []
    assert match_projects("Python 是什么？", prepare_projects([])) == []


def test_project_topics_do_not_change_filtering_catalog():
    from app.modules.recommendation_service import detect_technologies
    assert detect_technologies("RAG 向量检索 重排 多智能体") == []

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.interview_engine import begin, command, submit, unknown_answer

NOW = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)


def session():
    return {"status": "READY", "question_seconds": 180, "cursor": 0, "turns": [], "blind_topics": [], "skipped_plan_ids": [], "plan": [
        {"id": "a", "prompt": "RAG 如何重排？", "topics": ["RAG", "重排"], "kind": "BANK"},
        {"id": "b", "prompt": "RAG 如何评估效果？", "topics": ["RAG"], "kind": "BANK"},
        {"id": "c", "prompt": "Python 的 GIL 是什么？", "topics": ["Python"], "kind": "BANK"},
    ]}


def test_question_deadline_pause_resume_and_extension_are_server_based():
    state = session()
    begin(state, NOW)
    turn = state["turns"][-1]
    assert datetime.fromisoformat(turn["deadline_at"]) == NOW + timedelta(seconds=180)
    command(state, "PAUSE", NOW + timedelta(seconds=30))
    assert turn["remaining_seconds"] == 150
    command(state, "RESUME", NOW + timedelta(hours=1))
    assert datetime.fromisoformat(turn["deadline_at"]) == NOW + timedelta(hours=1, seconds=150)
    command(state, "EXTEND", NOW + timedelta(hours=1), 60)
    assert datetime.fromisoformat(turn["deadline_at"]) == NOW + timedelta(hours=1, seconds=210)
    assert turn["answer_status"] == "DRAFT"  # No automatic submission, even after deadline.
    submit(state, "我会先做向量检索，再重排。", "ANSWER", NOW + timedelta(hours=2))
    assert state["status"] == "REVIEWING" and turn["timed_out"]


def test_dont_know_immediately_changes_direction_and_blocks_further_same_topic_questions():
    state = session()
    begin(state, NOW)
    submit(state, "", "DONT_KNOW", NOW)
    assert state["status"] == "ACTIVE" and state["turns"][-1]["prompt"] == "Python 的 GIL 是什么？"
    assert state["turns"][0]["answer_status"] == "DONT_KNOW"
    assert state["skipped_plan_ids"] == ["b"]
    assert set(state["blind_topics"]) == {"RAG", "重排"}
    submit(state, "这题不会", "ANSWER", NOW)
    assert state["status"] == "COMPLETED"


@pytest.mark.parametrize("text", ["不会", "我不知道", "不了解", "暂时没接触过", "这题不会。", "我没用过", "这个不懂"])
def test_explicit_short_unknown_answers_are_recognized(text):
    assert unknown_answer(text)


def test_uncertainty_inside_a_real_answer_is_not_treated_as_complete_blindness():
    assert not unknown_answer("我不知道应该选哪一种，但可以先比较召回率和延迟。")
    state = session()
    begin(state, NOW)
    command(state, "PAUSE", NOW)
    with pytest.raises(ValueError):
        submit(state, "测试回答", "ANSWER", NOW)


def test_skip_moves_on_without_marking_a_whole_topic_unknown():
    state = session()
    begin(state, NOW)
    submit(state, "", "SKIP", NOW)
    assert state["turns"][-1]["id"] != state["turns"][0]["id"]
    assert state["turns"][-1]["prompt"] == "RAG 如何评估效果？" and not state["blind_topics"]


def test_extension_after_deadline_keeps_the_time_expiry_record():
    state = session()
    begin(state, NOW)
    command(state, "EXTEND", NOW + timedelta(seconds=181), 60)
    submit(state, "先召回，再重排。", "ANSWER", NOW + timedelta(seconds=182))
    assert state["turns"][0]["timed_out"] is True

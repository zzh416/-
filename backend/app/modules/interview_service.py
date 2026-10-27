"""Plan and review jobs for immutable, owner-scoped timed interview sessions."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from app.adapters.glm_text import TextModelError, TextModelNotConfigured
from app.adapters.interview_coach import generate_interview_plan, review_interview_answer
from app.adapters.text_provider import get_text_model_config
from app.db import SessionLocal
from app.models import InterviewSession, RecommendationRun
from app.modules import interview_engine as engine
from app.modules.project_matching import _topics, match_projects, prepare_projects
from app.modules.interview_planning import PlanningCancelled, _payload, allowed, complete_plan, constraints, make_plan, projects, prompt_hash
from app.modules.recommendation_context import (
    profile_context,
    recommendation_run_is_current,
    rule_context,
)


def now():
    return datetime.now(timezone.utc)


def current(db, row):
    if row.state.get("question_source") == "AI":
        _, version, _, _ = profile_context(db, row.owner_id)
        _, _, fingerprint = rule_context(db, row.owner_id)
        return row.state.get("profile_version") == version and row.state.get("rules_fingerprint") == fingerprint
    run = db.get(RecommendationRun, row.recommendation_run_id)
    return bool(
        run
        and run.owner_id == row.owner_id
        and recommendation_run_is_current(db, run, row.owner_id)
    )


def save(db, row, state):
    row.state, row.status = state, state["status"]
    row.version += 1
    db.commit()


def process_interview(session_id: UUID, *, planner=generate_interview_plan, reviewer=review_interview_answer):
    with SessionLocal() as db:
        row = db.scalar(select(InterviewSession).where(InterviewSession.id == session_id).with_for_update())
        if not row or row.status not in {"PLANNING", "REVIEWING"} or row.job_token is not None:
            return
        state = deepcopy(row.state)
        phase = row.status
        token = uuid4()
        row.job_token, row.job_started_at = token, now()
        state["job_stage"] = "PROCESSING"
        save(db, row, state)
        valid = current(db, row)
    config = get_text_model_config()
    state["model"] = config.model
    payload = _payload(state)
    try:
        if not valid:
            raise TextModelError("INTERVIEW_INPUT_STALE", "画像、偏好或题库已变化，请重新建立面试", retryable=False)
        if phase == "PLANNING":
            def check_live():
                with SessionLocal() as db:
                    live = db.get(InterviewSession, session_id)
                    if not live or live.job_token != token or live.status != phase:
                        raise PlanningCancelled()
                    if not current(db, live):
                        raise TextModelError("INTERVIEW_INPUT_STALE", "画像、偏好或题库已变化，请重新建立面试", retryable=False)

            def progress(plan, diagnostics):
                state["plan"] = deepcopy(plan)
                state["planning_diagnostics"] = deepcopy(diagnostics)
                state["job_stage"] = "SUPPLEMENTING" if diagnostics["model_calls"] > 1 else "PROCESSING"
                with SessionLocal() as db:
                    live = db.scalar(select(InterviewSession).where(InterviewSession.id == session_id).with_for_update())
                    if not live or live.job_token != token or live.status != phase:
                        raise PlanningCancelled()
                    save(db, live, deepcopy(state))

            state["plan"], state["planning_diagnostics"] = complete_plan(state, planner, config, progress=progress, check_live=check_live)
            if len(state["plan"]) < state["question_count"]:
                state["notice"] = f"本次自动补题后保留{len(state['plan'])}/{state['question_count']}道题，仍缺{state['question_count']-len(state['plan'])}道。可查看出题检查详情、继续补足，或使用现有题目开始。"
            else:
                state["notice"] = ""
            state["status"], state["planning_error"] = "READY", None
            state["candidates"] = []
        else:
            turn = state["turns"][-1]
            root = next(item for item in state["plan"] if item["id"] == turn["root_id"])
            project = next((item for item in projects(state) if item["id"] == root.get("project_id")), None)
            payload.update(question=turn["prompt"], answer=turn["answer"], root_question=root["prompt"],
                           reference_answer=turn.get("reference_answer"),
                           reference_answer_source_type=turn.get("reference_answer_source_type"), max_followups_reached=turn["depth"] >= 2,
                           conversation=[{"question": item["prompt"], "answer": item["answer"]} for item in state["turns"] if item["root_id"] == turn["root_id"] and item["id"] != turn["id"]])
            payload["projects"] = [{"id": project["id"], "name": project["name"], "description": project["description"]}] if project else []
            result = reviewer(payload, api_key=config.api_key, base_url=config.base_url, model=config.model, provider=config.provider)
            turn["feedback"] = result.model_dump(exclude={"followup", "change_topic"})
            turn["review_error"], turn["review_status"] = None, "DONE"
            turn["followup"] = None
            suggestion = result.followup
            existing = set(state["existing_prompt_hashes"]) | {prompt_hash(item["prompt"]) for item in state["plan"] + state["turns"]}
            if result.change_topic:
                state["blind_topics"] = sorted(set(state["blind_topics"]) | set(turn["topics"]))
                state["notice"] = "回答显示此方向暂不熟悉，已切换方向，不继续追问。"
                engine.advance(state, now())
            else:
                if suggestion and turn["depth"] < 2 and suggestion.answer_quote in turn["answer"] and allowed(state, suggestion.question) and prompt_hash(suggestion.question) not in existing:
                    grounded = bool(project and suggestion.project_quote and len(suggestion.project_quote.strip()) >= 4 and suggestion.project_quote in project["description"])
                    related = bool(set(_topics(suggestion.question)).intersection(turn["topics"]))
                    question_grounded = bool(suggestion.question_quote and len(suggestion.question_quote.strip()) >= 4 and suggestion.question_quote in turn["prompt"])
                    if (project and grounded and match_projects(suggestion.question, prepare_projects([project]))) or (not project and (related or question_grounded)):
                        turn["followup"] = {"prompt": suggestion.question, "answer_quote": suggestion.answer_quote, "project_quote": suggestion.project_quote if grounded else None, "question_quote": suggestion.question_quote if question_grounded else None}
                state["status"] = "AWAITING_NEXT"
        state["job_stage"] = "IDLE"
    except PlanningCancelled:
        return
    except (TextModelError, TextModelNotConfigured) as exc:
        _fail_state(state, phase, str(exc))
    except Exception:
        _fail_state(state, phase, "模型处理出现错误，回答已保留，可继续下一题或稍后重试")
    with SessionLocal() as db:
        row = db.scalar(select(InterviewSession).where(InterviewSession.id == session_id).with_for_update())
        if not row or row.job_token != token or row.status != phase:
            return
        if not current(db, row):
            state = deepcopy(row.state)
            _fail_state(state, phase, "画像、偏好或题库已变化，请重新建立面试")
        row.job_token = None
        save(db, row, state)


def _fail_state(state, phase, message):
    state["job_stage"] = "FAILED"
    if phase == "PLANNING":
        state["status"], state["planning_error"] = "PLAN_FAILED", message[:500]
    else:
        state["status"] = "AWAITING_NEXT"
        turn = state["turns"][-1]
        turn["review_error"], turn["followup"], turn["review_status"] = message[:500], None, "FAILED"

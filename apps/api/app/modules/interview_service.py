"""Plan and review jobs for immutable, owner-scoped timed interview sessions."""
from copy import deepcopy
import hashlib
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
from app.modules.question_extraction_service import initial_review_status
from app.modules.question_identity import question_identity
from app.modules.recommendation_service import PreferenceSnapshot, SkillSnapshot, build_recommendation
from app.routes.recommendations import _run_view


def now():
    return datetime.now(timezone.utc)


def current(db, row):
    run = db.get(RecommendationRun, row.recommendation_run_id)
    return bool(run and run.owner_id == row.owner_id and _run_view(db, run, row.owner_id).is_current)


def constraints(state):
    skills = [SkillSnapshot(**item) for item in state["profile_snapshot"]["skills"]]
    rules = [PreferenceSnapshot(item["rule_type"], item["canonical_technology"], item["technology"], item["category"], item["scope"]) for item in state["rule_snapshot"]]
    return skills, rules


def allowed(state, prompt):
    skills, rules = constraints(state)
    return initial_review_status(prompt) != "REJECTED" and build_recommendation(prompt=prompt, answer=None, skills=skills, rules=rules).decision == "INCLUDED"


def projects(state):
    return [item for item in state["profile_snapshot"].get("projects", []) if item["review_status"] == "CONFIRMED" and (not state["project_id"] or item["id"] == state["project_id"])]


def prompt_hash(prompt):
    return hashlib.sha256(question_identity(prompt).encode()).hexdigest()


def make_plan(state, result=None):
    pool = {item["id"]: item for item in state["candidates"]}
    selected = [(pool[str(item.item_id)], item.reason) for item in result.selected if str(item.item_id) in pool] if result else [(item, "沿用题库筛选结果") for item in state["candidates"]]
    plan, seen = [], set()
    for item, reason in selected:
        identity = question_identity(item["prompt"])
        if identity in seen or not allowed(state, item["prompt"]):
            continue
        seen.add(identity)
        plan.append({**item, "reason": reason, "kind": "BANK", "topics": sorted(_topics(item["prompt"])) or ["通用基础"]})
        if len(plan) >= state["question_count"]:
            break
    # Fill with valid bank questions first, even if the model selected too few.
    if len(plan) < state["question_count"] and result:
        for item in make_plan({**state, "question_count": state["question_count"]}):
            if question_identity(item["prompt"]) not in seen:
                plan.append(item); seen.add(question_identity(item["prompt"]))
            if len(plan) >= state["question_count"]:
                break
    project_map = {item["id"]: item for item in projects(state)}
    known = set(state["existing_prompt_hashes"]) | {prompt_hash(item["prompt"]) for item in plan}
    for item in result.new_questions if result else []:
        project = project_map.get(str(item.project_id))
        identity = prompt_hash(item.question)
        if len(plan) >= state["question_count"]:
            break
        if not project or identity in known or item.project_quote not in project["description"] or not allowed(state, item.question):
            continue
        if not match_projects(item.question, prepare_projects([project])):
            continue
        known.add(identity)
        plan.append({"id": str(uuid4()), "prompt": item.question, "kind": "AI", "reason": item.reason,
                     "project_id": project["id"], "project_name": project["name"], "project_quote": item.project_quote,
                     "reference_answer": None, "source_statistics": None, "topics": sorted(_topics(item.question)) or ["通用基础"]})
    return plan


def save(db, row, state):
    row.state, row.status = state, state["status"]
    row.version += 1
    db.commit()


def _payload(state):
    skills, rules = constraints(state)
    return {"target_role": state["profile_snapshot"].get("target_role"),
            "confirmed_skills": [item.canonical_name for item in skills if item.review_status == "CONFIRMED"],
            "excluded_technologies": [item.technology for item in rules if item.rule_type == "EXCLUDE" and item.scope in {"QUESTION", "BOTH"}],
            "projects": [{"id": item["id"], "name": item["name"], "description": item["description"]} for item in projects(state)]}


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
            payload.update(question_count=state["question_count"], candidates=[{"id": item["id"], "prompt": item["prompt"]} for item in state["candidates"]])
            result = planner(payload, api_key=config.api_key, base_url=config.base_url, model=config.model, provider=config.provider)
            state["plan"] = make_plan(state, result)
            if not state["plan"]:
                raise TextModelError("INTERVIEW_EMPTY_PLAN", "没有通过筛选的题目，请补充题库或项目", retryable=False)
            state["status"], state["planning_error"] = "READY", None
            state["candidates"] = []
        else:
            turn = state["turns"][-1]
            root = next(item for item in state["plan"] if item["id"] == turn["root_id"])
            project = next((item for item in projects(state) if item["id"] == root.get("project_id")), None)
            payload.update(question=turn["prompt"], answer=turn["answer"], root_question=root["prompt"],
                           reference_answer=turn.get("reference_answer"), max_followups_reached=turn["depth"] >= 2,
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

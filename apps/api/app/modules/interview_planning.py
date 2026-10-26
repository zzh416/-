"""Grounded interview planning, validation counters and bounded completion."""
from copy import deepcopy
import hashlib
from uuid import uuid4

from app.adapters.glm_text import TextModelError, TextModelNotConfigured
from app.modules.project_matching import _topics, match_projects, prepare_projects
from app.modules.question_extraction_service import initial_review_status
from app.modules.question_identity import question_identity
from app.modules.recommendation_service import PreferenceSnapshot, SkillSnapshot, _alias_pattern, _related_to_skill, build_recommendation, detect_technologies

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


def make_plan(state, result=None, *, previous_plan=None, diagnostics=None):
    pool = {item["id"]: item for item in state["candidates"]}
    source = state.get("question_source", "HYBRID")
    selected = []
    picked_ids = set()
    if source != "AI":
        if result is None:
            selected = [(item, "沿用题库筛选结果", False) for item in state["candidates"]]
        else:
            for item in result.selected:
                if str(item.item_id) in pool:
                    selected.append((pool[str(item.item_id)], item.reason, True))
                    picked_ids.add(str(item.item_id))
                else:
                    _reject(diagnostics, "UNKNOWN_CANDIDATE")
            selected.extend((item, "沿用题库筛选结果", False) for item in state["candidates"] if item["id"] not in picked_ids)
    elif result and result.selected:
        _reject(diagnostics, "WRONG_SOURCE", len(result.selected))
    plan = deepcopy(previous_plan or [])
    seen = {question_identity(item["prompt"]) for item in plan}
    for item, reason, from_model in selected:
        if len(plan) >= state["question_count"]:
            break
        identity = question_identity(item["prompt"])
        if identity in seen:
            if from_model:
                _reject(diagnostics, "DUPLICATE")
            continue
        if not allowed(state, item["prompt"]):
            _reject(diagnostics, "QUESTION_FILTER")
            continue
        seen.add(identity)
        plan.append({**item, "reason": reason, "kind": "BANK", "topics": sorted(_topics(item["prompt"])) or ["通用基础"]})
        if len(plan) >= state["question_count"]:
            break
    project_map = {item["id"]: item for item in projects(state)}
    known = set(state["existing_prompt_hashes"]) | {prompt_hash(item["prompt"]) for item in plan}
    for item in result.new_questions if result and source != "BANK" else []:
        project = project_map.get(str(item.project_id))
        identity = prompt_hash(item.question)
        if len(plan) >= state["question_count"]:
            break
        if identity in known:
            _reject(diagnostics, "DUPLICATE")
            continue
        if not allowed(state, item.question):
            _reject(diagnostics, "QUESTION_FILTER")
            continue
        basis = _question_basis(state, item, project, diagnostics)
        if basis is None:
            continue
        known.add(identity)
        plan.append({"id": str(uuid4()), "prompt": item.question, "kind": "AI", "reason": item.reason,
                     **basis,
                     "reference_answer": None, "source_statistics": None, "topics": sorted(_topics(item.question)) or ["通用基础"]})
    return plan


def _question_basis(state, item, project, diagnostics=None):
    if item.project_id is not None:
        if item.skill:
            _reject(diagnostics, "BASIS_CONFLICT")
            return None
        if not project:
            _reject(diagnostics, "BASIS_PROJECT")
            return None
        if not item.project_quote or item.project_quote not in project["description"]:
            _reject(diagnostics, "BASIS_QUOTE")
            return None
        if not match_projects(item.question, prepare_projects([project])):
            _reject(diagnostics, "BASIS_TOPIC")
            return None
        return {"project_id": project["id"], "project_name": project["name"], "project_quote": item.project_quote, "skill_basis": None}
    if state["project_id"] or item.project_quote or not item.skill:
        _reject(diagnostics, "BASIS_SCOPE")
        return None
    skill = next((s for s in state["profile_snapshot"]["skills"] if s["review_status"] == "CONFIRMED"
                  and item.skill.casefold() in {s["canonical_name"].casefold(), s["display_name"].casefold()}), None)
    if not skill:
        _reject(diagnostics, "BASIS_SKILL")
        return None
    related = any(_related_to_skill(skill["canonical_name"], t.canonical_name) for t in detect_technologies(item.question))
    if not related and not any(_alias_pattern(name).search(item.question) for name in (skill["canonical_name"], skill["display_name"])):
        _reject(diagnostics, "BASIS_TOPIC")
        return None
    return {"project_id": None, "project_name": None, "project_quote": None, "skill_basis": skill["display_name"]}


def _payload(state):
    skills, rules = constraints(state)
    return {"target_role": state["profile_snapshot"].get("target_role"),
            "selected_project_id": state.get("project_id"),
            "allowed_basis": ["PROJECT"] if state.get("project_id") else ["SKILL", "PROJECT"],
            "question_source": state.get("question_source", "HYBRID"),
            "confirmed_skills": [item.canonical_name for item in skills if item.review_status == "CONFIRMED"],
            "confirmed_skill_details": [{"canonical_name": item.canonical_name, "display_name": item.display_name} for item in skills if item.review_status == "CONFIRMED"],
            "excluded_technologies": [item.technology for item in rules if item.rule_type == "EXCLUDE" and item.scope in {"QUESTION", "BOTH"}],
            "projects": [{"id": item["id"], "name": item["name"], "description": item["description"]} for item in projects(state)]}


class PlanningCancelled(Exception):
    """The user ended or replaced this planning attempt."""


def _reject(diagnostics, code, count=1):
    if diagnostics is not None:
        counts = diagnostics["rejection_counts"]
        counts[code] = counts.get(code, 0) + count


def complete_plan(state, planner, config, *, progress=None, check_live=None):
    """Preserve valid questions and fill missing slots with at most three calls."""
    target = state["question_count"]
    plan = deepcopy(state.get("plan", []))[:target]
    diag = {"target_count": target, "accepted_count": len(plan), "model_calls": 0,
            "returned_count": 0, "format_issue_count": 0, "rejection_counts": {},
            "errors": [], "schema_paths": [], "exhausted": False}
    for request_index in range(3):
        if len(plan) >= target:
            break
        if check_live:
            check_live()
        payload = _payload(state)
        selected_ids = {item["id"] for item in plan}
        payload.update(question_count=target - len(plan), desired_total=target,
            existing_questions=[item["prompt"] for item in plan],
            candidates=[{"id": item["id"], "prompt": item["prompt"]} for item in state["candidates"] if item["id"] not in selected_ids],
            repair_feedback={"rejection_counts": diag["rejection_counts"].copy(),
                             "format_errors": diag["errors"][-1:], "schema_paths": diag["schema_paths"][-8:]})
        diag["model_calls"] += 1
        try:
            result = planner(payload, api_key=config.api_key, base_url=config.base_url, model=config.model, provider=config.provider)
            summary = result.validation_summary
            diag["returned_count"] += summary.get("returned_items", len(result.selected) + len(result.new_questions))
            invalid = summary.get("invalid_items", 0)
            diag["format_issue_count"] += invalid
            if invalid:
                _reject(diag, "SCHEMA", invalid)
            diag["schema_paths"] = (diag["schema_paths"] + summary.get("paths", []))[-12:]
            if not result.selected and not result.new_questions and not invalid:
                _reject(diag, "EMPTY_RESPONSE")
            plan = make_plan(state, result, previous_plan=plan, diagnostics=diag)
        except (TextModelError, TextModelNotConfigured) as exc:
            code = getattr(exc, "code", "INTERVIEW_MODEL_NOT_CONFIGURED")
            diag["errors"].append(code)
            repairable = code in {"INTERVIEW_INVALID_RESPONSE", "INTERVIEW_OUTPUT_TRUNCATED"}
            if repairable:
                diag["format_issue_count"] += 1
            diag["accepted_count"] = len(plan)
            diag["exhausted"] = not repairable or request_index == 2
            if progress:
                progress(plan, diag)
            if not repairable:
                if not plan:
                    raise
                break
            continue
        diag["accepted_count"] = len(plan)
        diag["exhausted"] = len(plan) < target and request_index == 2
        if progress:
            progress(plan, diag)
    diag["accepted_count"] = len(plan)
    diag["exhausted"] = len(plan) < target
    if not plan:
        if progress:
            progress(plan, diag)
        raise TextModelError("INTERVIEW_EMPTY_PLAN", "本次补题后仍没有通过依据和偏好校验的题目，请核对技能、项目和排除规则", retryable=False)
    return plan, diag



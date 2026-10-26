"""Ground generated questions in two saved inputs; never treat them as source quotes."""
import re
from collections import Counter
from uuid import uuid4

from app.modules.project_matching import match_projects, prepare_projects
from app.modules.question_extraction_service import initial_review_status
from app.modules.question_identity import question_identity
from app.modules.recommendation_service import build_recommendation


def validate_followups(project, seeds, candidates, *, skills, rules, existing_prompts=()):
    seed_map = {item["id"]: item for item in seeds}
    seed_order = {item["id"]: index for index, item in enumerate(seeds)}
    seen = {question_identity(item["prompt"]) for item in seeds} | {question_identity(prompt) for prompt in existing_prompts}
    levels, parents, accepted, rejected = {}, {}, [], Counter()
    prepared = prepare_projects([project])
    for item in sorted(candidates, key=lambda item: (seed_order.get(str(item.seed_item_id), 999), item.level)):
        seed_id = str(item.seed_item_id)
        seed = seed_map.get(seed_id)
        reason = None
        if seed is None:
            reason = "unknown_seed"
        elif item.project_quote not in project["description"] or item.seed_quote not in seed["prompt"]:
            reason = "source_mismatch"
        elif question_identity(item.question) in seen:
            reason = "duplicate"
        elif initial_review_status(item.question) == "REJECTED" or not re.search(r"[?？]|如何|什么|哪些|是否|为什么|介绍|解释|比较|分析", item.question):
            reason = "not_question"
        elif build_recommendation(prompt=item.question, answer=None, skills=skills, rules=rules).decision != "INCLUDED":
            reason = "excluded"
        elif not match_projects(item.question, prepared):
            reason = "unrelated_project"
        elif item.level != levels.get(seed_id, 0) + 1:
            reason = "broken_chain"
        if reason:
            rejected[reason] += 1
            continue
        project_start = project["description"].index(item.project_quote)
        seed_start = seed["prompt"].index(item.seed_quote)
        result = {**item.model_dump(mode="json"), "id": str(uuid4()), "parent_id": parents.get(seed_id),
                  "project_start": project_start, "project_end": project_start + len(item.project_quote),
                  "seed_start": seed_start, "seed_end": seed_start + len(item.seed_quote)}
        accepted.append(result)
        seen.add(question_identity(item.question))
        levels[seed_id], parents[seed_id] = item.level, result["id"]
    return accepted, dict(rejected)

"""Pure timed interview transitions; unknown answers never schedule a model call."""
import re
from datetime import datetime, timedelta
from uuid import uuid4


def unknown_answer(text: str) -> bool:
    return bool(re.fullmatch(
        r"(?:(?:这题|这个|这部分|这方面|我|目前|暂时|完全|确实)[，,：:\s]*)*(?:不(?:太)?(?:会|知道|了解|清楚|熟悉|懂)|没(?:有)?(?:接触|了解|学|做|用|听说|研究)过?|答不上来)[。.!！?？\s]*", text.strip()))


def remaining(turn, now):
    return max(0, (datetime.fromisoformat(turn["deadline_at"]) - now).total_seconds()) if turn.get("deadline_at") else turn.get("remaining_seconds", 0)


def _new_turn(state, item, now, *, depth=0, root_id=None):
    state["turns"].append({
        **item, "root_id": root_id or item["id"], "id": str(uuid4()), "depth": depth,
        "draft": "", "answer": "", "answer_status": "DRAFT", "feedback": None, "followup": None,
        "started_at": now.isoformat(), "deadline_at": (now + timedelta(seconds=state["question_seconds"])).isoformat(),
        "allocated_seconds": state["question_seconds"], "remaining_seconds": state["question_seconds"], "elapsed_seconds": 0,
        "timed_out": False, "review_status": "NONE", "review_attempts": 0,
    })
    state["status"] = "ACTIVE"


def advance(state, now):
    blind = set(state["blind_topics"])
    while state["cursor"] < len(state["plan"]):
        item = state["plan"][state["cursor"]]
        state["cursor"] += 1
        if blind.intersection(item["topics"]):
            state["skipped_plan_ids"].append(item["id"])
            continue
        _new_turn(state, item, now)
        return
    state["status"] = "COMPLETED"
    state["finished_reason"] = "本轮已完成；已跳过涉及盲区的同主题题目。" if blind else "本轮题目已完成。"


def begin(state, now):
    if state["status"] != "READY" or not state["plan"]:
        raise ValueError("当前没有可开始的面试计划")
    state["notice"] = ""
    advance(state, now)


def submit(state, text, action, now):
    if state["status"] != "ACTIVE":
        raise ValueError("请先恢复面试，或等待当前回答处理完成")
    turn = state["turns"][-1]
    turn["timed_out"] = turn["timed_out"] or remaining(turn, now) <= 0
    turn["remaining_seconds"] = remaining(turn, now)
    turn["elapsed_seconds"] = max(0, round(turn["allocated_seconds"] - turn["remaining_seconds"]))
    turn["deadline_at"] = None
    turn["answer"], turn["draft"] = text.strip(), text
    if action == "DONT_KNOW" or (action == "ANSWER" and unknown_answer(text)):
        turn["answer_status"] = "DONT_KNOW"
        state["blind_topics"] = sorted(set(state["blind_topics"]) | set(turn["topics"]))
        state["notice"] = "已记录这道题暂时不会，已切换方向，不再沿该知识点追问。"
        advance(state, now)
    elif action == "SKIP":
        turn["answer_status"] = "SKIPPED"
        state["notice"] = "已跳过当前题目。"
        advance(state, now)
    else:
        if not text.strip():
            raise ValueError("请填写回答，或选择不会／跳过")
        turn["answer_status"], turn["review_status"] = "ANSWERED", "QUEUED"
        state["status"] = "REVIEWING"


def command(state, action, now, seconds=60):
    status = state["status"]
    if action == "FINISH":
        if status == "COMPLETED":
            raise ValueError("本轮面试已经结束")
        state["status"], state["finished_reason"] = "COMPLETED", "用户结束本轮面试。"
        return
    if action == "START":
        begin(state, now)
        return
    if not state["turns"]:
        raise ValueError("当前没有作答题目")
    turn = state["turns"][-1]
    if action == "PAUSE" and status == "ACTIVE":
        turn["remaining_seconds"] = remaining(turn, now)
        turn["deadline_at"] = None
        state["status"] = "PAUSED"
    elif action == "RESUME" and status == "PAUSED":
        turn["deadline_at"] = (now + timedelta(seconds=turn["remaining_seconds"])).isoformat()
        state["status"] = "ACTIVE"
    elif action == "EXTEND" and status in {"ACTIVE", "PAUSED"}:
        if turn["allocated_seconds"] + seconds > 900:
            raise ValueError("单题累计时长最多15分钟")
        turn["timed_out"] = turn["timed_out"] or remaining(turn, now) <= 0
        turn["remaining_seconds"] = remaining(turn, now) + seconds
        turn["allocated_seconds"] += seconds
        if status == "ACTIVE":
            turn["deadline_at"] = (now + timedelta(seconds=turn["remaining_seconds"])).isoformat()
    elif action == "NEXT" and status == "AWAITING_NEXT":
        advance(state, now)
    elif action == "FOLLOWUP" and status == "AWAITING_NEXT" and turn.get("followup") and turn["depth"] < 2:
        root = next(item for item in state["plan"] if item["id"] == turn["root_id"])
        item = {**root, **turn["followup"], "kind": "AI_FOLLOWUP", "reference_answer": None, "reference_answer_source_type": None}
        _new_turn(state, item, now, depth=turn["depth"] + 1, root_id=turn["root_id"])
    else:
        raise ValueError("当前状态不能执行这个操作")

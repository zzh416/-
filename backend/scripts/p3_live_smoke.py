"""Run a disposable P3 extraction-quality smoke test through the live API and worker."""
from __future__ import annotations

import os
import time
from uuid import uuid4

import httpx


BASE_URL = os.getenv("P3_SMOKE_BASE_URL", "http://127.0.0.1:9000/api/v1")
TERMINAL_STATUSES = {
    "SUCCEEDED",
    "PARTIAL_SUCCESS",
    "WAITING_FOR_CONFIGURATION",
    "FAILED",
}
SAMPLE = """## P3 混合题答验收样本

### 1. Python 的 GIL 是什么？

GIL 是 CPython 解释器中的全局解释器锁，同一时刻只允许一个线程执行 Python 字节码。

### 2. Java synchronized 的作用是什么？

### 3. 数据库事务的 ACID 分别是什么？

ACID 分别是原子性、一致性、隔离性和持久性。

### 4. MySQL 的索引下推是什么？
"""
EXPECTED = {
    "GIL": {"answer": "CPython"},
    "synchronized": {"answer": None},
    "ACID": {"answer": "原子性"},
    "索引下推": {"answer": None},
}


def _wait_for_api(client: httpx.Client) -> None:
    for _ in range(30):
        try:
            if client.get(f"{BASE_URL}/health/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("API did not become ready")


def _wait_for_run(client: httpx.Client, run_id: str) -> dict:
    for _ in range(240):
        response = client.get(f"{BASE_URL}/question-extraction-runs/{run_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in TERMINAL_STATUSES:
            return payload
        time.sleep(0.5)
    raise RuntimeError(f"question extraction run {run_id} did not finish")


def _assert_quality(review: dict) -> None:
    prompts = [item["prompt"] for item in review["items"]]
    suspected = [item["quote"] for item in review["suspectedMisses"]]
    discoverable = prompts + suspected
    missing_keywords = [
        keyword
        for keyword in EXPECTED
        if not any(keyword.casefold() in text.casefold() for text in discoverable)
    ]
    assert not missing_keywords, (
        f"undiscovered expected questions: {missing_keywords}; "
        f"extracted={prompts}; suspected={suspected}"
    )

    for keyword, expectation in EXPECTED.items():
        matches = [
            item
            for item in review["items"]
            if keyword.casefold() in item["prompt"].casefold()
        ]
        if not matches:
            continue
        assert len(matches) == 1, f"duplicate extracted question for {keyword}"
        answer = matches[0]["answer"]
        expected_answer_fragment = expectation["answer"]
        if expected_answer_fragment is None:
            assert answer is None, f"question {keyword} received an unexpected answer"
        else:
            assert answer is not None, f"question {keyword} lost its source answer"
            assert expected_answer_fragment in answer["content"]


def main() -> None:
    collection_id: str | None = None
    asset_id: str | None = None
    question_ids: list[str] = []
    with httpx.Client(timeout=20) as client:
        _wait_for_api(client)
        collection = client.post(
            f"{BASE_URL}/collections",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "name": f"P3 live smoke {uuid4()}",
                "purpose": "INTERVIEW_EXPERIENCE",
            },
        )
        collection.raise_for_status()
        collection_id = collection.json()["id"]
        try:
            asset = client.post(
                f"{BASE_URL}/collections/{collection_id}/assets/text",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "title": "P3 混合题答验收样本",
                    "content": SAMPLE,
                    "sourceFormat": "PASTED_TEXT",
                },
            )
            asset.raise_for_status()
            asset_id = asset.json()["id"]
            if os.getenv("P3_SMOKE_DEBUG"):
                detail = client.get(f"{BASE_URL}/assets/{asset_id}")
                detail.raise_for_status()
                print(
                    "P3 source chunks:",
                    [
                        (chunk["ordinal"], chunk["content"])
                        for chunk in detail.json()["chunks"]
                    ],
                )
            run = client.post(
                f"{BASE_URL}/assets/{asset_id}/question-extraction-runs",
                headers={"Idempotency-Key": str(uuid4())},
            )
            run.raise_for_status()
            result = _wait_for_run(client, run.json()["id"])
            if result["status"] == "WAITING_FOR_CONFIGURATION":
                print("P3 live extraction: SKIPPED because the selected text model is not configured")
                return
            assert result["status"] in {"SUCCEEDED", "PARTIAL_SUCCESS"}, result
            review_response = client.get(
                f"{BASE_URL}/question-extraction-runs/{result['id']}/review-items",
                params={"pageSize": 100},
            )
            review_response.raise_for_status()
            review = review_response.json()
            question_ids.extend(item["questionId"] for item in review["items"])
            _assert_quality(review)
            print(
                "P3 mixed extraction: "
                f"{result['status']} · extracted={review['total']} · "
                f"suspected_misses={len(review['suspectedMisses'])} · "
                f"answered={result['answeredCount']} · missing={result['missingCount']}"
            )
        finally:
            if collection_id is not None:
                remaining = client.get(
                    f"{BASE_URL}/questions",
                    params={"collectionId": collection_id, "pageSize": 100},
                )
                if remaining.is_success:
                    question_ids.extend(
                        item["id"] for item in remaining.json()["items"]
                    )
            for question_id in set(question_ids):
                client.delete(f"{BASE_URL}/questions/{question_id}")
            if asset_id is not None:
                client.delete(f"{BASE_URL}/assets/{asset_id}")
            if collection_id is not None:
                client.delete(f"{BASE_URL}/collections/{collection_id}")


if __name__ == "__main__":
    main()

"""Run the P4 list/search benchmark in a disposable PostgreSQL database."""
from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


QUESTION_COUNT = 5_000
CHUNK_COUNT = 20_000
ITERATIONS = 30
LOCAL_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
THRESHOLDS_MS = {
    "first_page": 1_000.0,
    "answered_filter": 1_000.0,
    "keyword_search": 2_000.0,
    "deep_page": 1_000.0,
}


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _insert_batches(session, model, rows: list[dict], batch_size: int = 1_000) -> None:
    for start in range(0, len(rows), batch_size):
        session.execute(insert(model), rows[start : start + batch_size])


def _seed() -> None:
    from app.db import SessionLocal
    from app.models import Answer, Asset, AssetVersion, Collection, Question, TextChunk

    collection_id = uuid4()
    asset_id = uuid4()
    asset_version_id = uuid4()
    question_ids = [uuid4() for _ in range(QUESTION_COUNT)]

    with SessionLocal() as session:
        session.execute(
            insert(Collection),
            [
                {
                    "id": collection_id,
                    "owner_id": LOCAL_USER_ID,
                    "name": "P4 隔离性能基准",
                    "purpose": "KNOWLEDGE_BASE",
                }
            ],
        )
        session.execute(
            insert(Asset),
            [
                {
                    "id": asset_id,
                    "owner_id": LOCAL_USER_ID,
                    "collection_id": collection_id,
                    "kind": "LIBRARY",
                    "title": "P4 性能来源",
                    "source_format": "PASTED_TEXT",
                    "status": "READY",
                    "current_version": 1,
                }
            ],
        )
        session.execute(
            insert(AssetVersion),
            [
                {
                    "id": asset_version_id,
                    "asset_id": asset_id,
                    "version": 1,
                    "content": "P4 benchmark source",
                }
            ],
        )
        chunk_rows = [
            {
                "id": uuid4(),
                "asset_version_id": asset_version_id,
                "ordinal": index,
                "content": f"P4 来源块 {index}，用于验证两万段资料下的数据库容量。",
                "start_offset": index * 32,
                "end_offset": index * 32 + 31,
                "block_type": "TEXT",
                "locator": {"benchmark": True, "ordinal": index},
            }
            for index in range(CHUNK_COUNT)
        ]
        _insert_batches(session, TextChunk, chunk_rows)

        question_rows = [
            {
                "id": question_id,
                "owner_id": LOCAL_USER_ID,
                "collection_id": collection_id,
                "source_type": "USER_WRITTEN",
                "prompt": (
                    f"P4 第 {index} 道 Python 面试题 benchmark-keyword"
                    if index % 10 == 0
                    else f"P4 第 {index} 道通用面试题"
                ),
                "version": 1,
            }
            for index, question_id in enumerate(question_ids)
        ]
        _insert_batches(session, Question, question_rows)

        answer_rows = [
            {
                "id": uuid4(),
                "question_id": question_id,
                "owner_id": LOCAL_USER_ID,
                "content": f"P4 基准答案 {index}",
                "source_type": "USER_WRITTEN",
                "version": 1,
            }
            for index, question_id in enumerate(question_ids)
            if index % 2 == 0
        ]
        _insert_batches(session, Answer, answer_rows)
        session.commit()


def _measure(client, path: str) -> dict[str, float]:
    for _ in range(3):
        response = client.get(path)
        response.raise_for_status()

    samples: list[float] = []
    for _ in range(ITERATIONS):
        started_at = time.perf_counter()
        response = client.get(path)
        elapsed_ms = (time.perf_counter() - started_at) * 1_000
        response.raise_for_status()
        payload = response.json()
        if not payload.get("items"):
            raise RuntimeError(f"benchmark endpoint returned no items: {path}")
        samples.append(elapsed_ms)
    return {
        "p50Ms": round(statistics.median(samples), 2),
        "p95Ms": round(_percentile(samples, 0.95), 2),
        "maxMs": round(max(samples), 2),
    }


def _run_child() -> int:
    from fastapi.testclient import TestClient

    from app.main import app

    started_at = time.perf_counter()
    _seed()
    seed_seconds = time.perf_counter() - started_at
    cases = {
        "first_page": "/api/v1/questions?pageSize=20",
        "answered_filter": "/api/v1/questions?answerState=ANSWERED&pageSize=20",
        "keyword_search": "/api/v1/questions?q=benchmark-keyword&pageSize=20",
        "deep_page": "/api/v1/questions?page=200&pageSize=20",
    }
    with TestClient(app) as client:
        results = {name: _measure(client, path) for name, path in cases.items()}

    failures = [
        name
        for name, metrics in results.items()
        if metrics["p95Ms"] > THRESHOLDS_MS[name]
    ]
    report = {
        "dataset": {"questions": QUESTION_COUNT, "textChunks": CHUNK_COUNT},
        "iterationsPerCase": ITERATIONS,
        "seedSeconds": round(seed_seconds, 2),
        "thresholdsMs": THRESHOLDS_MS,
        "results": results,
        "passed": not failures,
        "failedCases": failures,
    }
    print("P4_PERFORMANCE_RESULT=" + json.dumps(report, ensure_ascii=False))
    return 1 if failures else 0


def _run_parent() -> int:
    sys.path.insert(0, str(Path.cwd()))
    from app.config import settings

    database_name = "bagu_p4_perf_" + uuid4().hex
    database_url = settings.sqlalchemy_database_url
    admin_engine = create_engine(
        database_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    created = False
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
            created = True
        env = dict(
            os.environ,
            DATABASE_URL=database_url.set(database=database_name).render_as_string(
                hide_password=False
            ),
            DATA_DIR=os.path.join(os.getenv("TMPDIR", "/tmp"), database_name),
            GLM_API_KEY="",
            DEEPSEEK_API_KEY="",
            EMBEDDING_API_KEY="",
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            check=True,
        )
        result = subprocess.run([sys.executable, __file__, "--child"], env=env)
        return result.returncode
    finally:
        if created:
            with admin_engine.connect() as connection:
                connection.exec_driver_sql(
                    f'DROP DATABASE "{database_name}" WITH (FORCE)'
                )
            print("P4 disposable performance database removed.", flush=True)
        admin_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(_run_child() if "--child" in sys.argv else _run_parent())

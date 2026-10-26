"""Regression scenarios for current-source collection during supplemental answers."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db import SessionLocal
from app.models import AIAnswerRun, Answer, Asset, AssetVersion, Question, QuestionExtractionRun, QuestionOccurrence, TextChunk, User
from app.modules.ai_answer_service import InputContext, attach_answers, fingerprint
from app.schemas.questions import QuestionView


@pytest.fixture
def audit_owner():
    owner_id = uuid4()
    with SessionLocal() as db:
        db.add(User(id=owner_id, display_name="补答来源审计"))
        db.commit()
    yield owner_id
    with SessionLocal() as db:
        db.delete(db.get(User, owner_id))
        db.commit()


def test_current_source_is_not_crowded_out_by_three_historical_versions(audit_owner):
    with SessionLocal() as db:
        asset = Asset(owner_id=audit_owner, title="多次修订资料", source_format="PASTED_TEXT", current_version=4)
        db.add(asset)
        db.flush()
        question = Question(owner_id=audit_owner, source_type="SOURCE_EXTRACTED", prompt="Python生成器如何使用？")
        db.add(question)
        db.flush()
        current_chunk = None
        for version_number in range(1, 5):
            content = f"Python生成器如何使用？\n第{version_number}版：生成器按需产生数据。"
            version = AssetVersion(asset_id=asset.id, version=version_number, content=content)
            chunk = TextChunk(ordinal=0, content=content, start_offset=0, end_offset=len(content))
            version.chunks.append(chunk)
            db.add(version)
            db.flush()
            if version_number == 1:
                question.source_chunk_id = chunk.id
            run = QuestionExtractionRun(owner_id=audit_owner, asset_id=asset.id, asset_version_id=version.id, status="SUCCEEDED")
            db.add(run)
            db.flush()
            db.add(QuestionOccurrence(question_id=question.id, extraction_run_id=run.id, asset_version_id=version.id,
                source_chunk_id=chunk.id, question_start=0, question_end=len(question.prompt), question_quote=question.prompt,
                confidence=1.0, review_status="CONFIRMED", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=version_number)))
            current_chunk = chunk
        db.commit()
        sources = InputContext(db, audit_owner, [question]).sources[question.id]
        assert [row["chunk_id"] for row in sources] == [str(current_chunk.id)]
        assert "第4版" in sources[0]["content"]


def test_multiblock_answer_source_spans_fill_available_citation_slots(audit_owner):
    with SessionLocal() as db:
        asset = Asset(owner_id=audit_owner, title="多段答案", source_format="PASTED_TEXT", current_version=1)
        version = AssetVersion(version=1, content="Python生成器如何使用？\n按需产生值。\n遍历完需要重新创建。")
        chunks = [TextChunk(ordinal=i, content=text, start_offset=0, end_offset=len(text))
                  for i, text in enumerate(["Python生成器如何使用？", "按需产生值。", "遍历完需要重新创建。"])]
        version.chunks.extend(chunks)
        asset.versions.append(version)
        db.add(asset)
        db.flush()
        question = Question(owner_id=audit_owner, source_type="SOURCE_EXTRACTED", prompt=chunks[0].content, source_chunk_id=chunks[0].id)
        run = QuestionExtractionRun(owner_id=audit_owner, asset_id=asset.id, asset_version_id=version.id, status="SUCCEEDED")
        db.add_all([question, run])
        db.flush()
        spans = [{"sourceChunkId": str(c.id), "blockOrdinal": c.ordinal, "start": 0, "end": len(c.content), "quote": c.content} for c in chunks[1:]]
        db.add(QuestionOccurrence(question_id=question.id, extraction_run_id=run.id, asset_version_id=version.id,
            source_chunk_id=chunks[0].id, answer_source_chunk_id=chunks[1].id, question_start=0,
            question_end=len(question.prompt), question_quote=question.prompt, confidence=1.0,
            review_status="CONFIRMED", answer_spans=list(reversed(spans))))
        db.commit()
        sources = InputContext(db, audit_owner, [question]).sources[question.id]
        assert [row["chunk_id"] for row in sources] == [str(c.id) for c in chunks]


def test_only_owned_current_sources_count_towards_three_block_limit(audit_owner):
    with SessionLocal() as db:
        foreign_owner = User(display_name="来源隔离用户")
        db.add(foreign_owner)
        db.flush()
        try:
            asset = Asset(owner_id=audit_owner, title="当前资料", source_format="PASTED_TEXT", current_version=2)
            current = AssetVersion(version=2, content="当前资料")
            chunks = [TextChunk(ordinal=i, content=f"当前第{i}段", start_offset=0, end_offset=5) for i in range(4)]
            current.chunks.extend(chunks)
            old = AssetVersion(version=1, content="过期资料")
            old.chunks.append(TextChunk(ordinal=0, content="过期资料", start_offset=0, end_offset=4))
            asset.versions.extend([old, current])
            foreign = Asset(owner_id=foreign_owner.id, title="其他用户资料", source_format="PASTED_TEXT", current_version=1)
            foreign_version = AssetVersion(version=1, content="其他用户的答案")
            foreign_version.chunks.append(TextChunk(ordinal=0, content="其他用户的答案", start_offset=0, end_offset=8))
            foreign.versions.append(foreign_version)
            db.add_all([asset, foreign])
            db.flush()
            question = Question(owner_id=audit_owner, source_type="SOURCE_EXTRACTED", prompt="如何迭代？", source_chunk_id=old.chunks[0].id)
            run = QuestionExtractionRun(owner_id=audit_owner, asset_id=asset.id, asset_version_id=current.id, status="SUCCEEDED")
            db.add_all([question, run])
            db.flush()
            spans = [{"sourceChunkId": str(c.id), "blockOrdinal": c.ordinal} for c in reversed(chunks[1:])]
            spans.extend([{"sourceChunkId": "invalid-legacy-id"}, {"sourceChunkId": str(old.chunks[0].id)}])
            db.add(QuestionOccurrence(question_id=question.id, extraction_run_id=run.id, asset_version_id=current.id,
                source_chunk_id=chunks[0].id, answer_source_chunk_id=foreign_version.chunks[0].id,
                question_start=0, question_end=5, question_quote=question.prompt, confidence=1.0,
                review_status="CONFIRMED", answer_spans=spans))
            db.commit()
            sources = InputContext(db, audit_owner, [question]).sources[question.id]
            assert [row["chunk_id"] for row in sources] == [str(c.id) for c in chunks[:3]]
            assert all(row["content"].startswith("当前第") for row in sources)
        finally:
            db.delete(foreign_owner)
            db.commit()


@pytest.mark.parametrize("latest_status,latest_review", [("SUCCEEDED", "DRAFT"), ("FAILED", "DRAFT"), ("SUCCEEDED", "DISCARDED")])
def test_confirmed_ai_remains_addressable_when_original_answer_has_priority(audit_owner, latest_status, latest_review):
    with SessionLocal() as db:
        question = Question(owner_id=audit_owner, source_type="USER_WRITTEN", prompt="Python生成器是什么？")
        question.answer = Answer(owner_id=audit_owner, content="原来的人工答案", source_type="USER_WRITTEN")
        db.add(question)
        db.flush()
        snapshot = InputContext(db, audit_owner, [question]).snapshot(question, False, False)
        kwargs = dict(owner_id=audit_owner, question_id=question.id, question_version=question.version,
            used_profile=False, used_note=False, input_snapshot=snapshot, input_fingerprint=fingerprint(snapshot),
            content="AI参考答案", basis="GENERAL_KNOWLEDGE")
        first = AIAnswerRun(**kwargs, status="SUCCEEDED", review_status="CONFIRMED", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        latest = AIAnswerRun(**kwargs, status=latest_status, review_status=latest_review, created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        db.add_all([first, latest])
        db.commit()
        attach_answers(db, [question])
        result = QuestionView.model_validate(question).model_dump(mode="json", by_alias=True)
        assert result["aiAnswer"]["id"] == str(latest.id)
        assert result["effectiveAnswer"]["sourceType"] == "USER_WRITTEN"
        assert result["effectiveAnswer"]["aiAnswerId"] is None
        assert result["confirmedAiAnswerId"] == str(first.id)
        first.review_status = "DISCARDED"
        db.commit()
        attach_answers(db, [question])
        assert QuestionView.model_validate(question).model_dump(by_alias=True)["confirmedAiAnswerId"] is None

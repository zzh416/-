"""Aggregate current, non-rejected sources across exact-equivalent questions."""
from collections import defaultdict
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AssetVersion, Collection, Question, QuestionExtractionRun, QuestionOccurrence, TextChunk
from app.modules.question_identity import question_identity
from app.modules.source_calendar import local_today
from app.schemas.sources import QuestionSourceView, SourceStatistics


def source_identity(asset: Asset) -> str:
    if asset.source_url:
        parsed = urlsplit(asset.source_url)
        query = sorted(
            (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"spm", "xsec_token", "xsec_source"}
        )
        # Preserve routing fragments and content query parameters (e.g. chapter/id).
        return "url:" + urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", urlencode(query), parsed.fragment))
    return "content:" + asset.content_sha256 if asset.content_sha256 else "asset:" + str(asset.id)


def question_source_statistics(db: Session, owner_id: UUID, questions: list[Question]) -> dict[UUID, SourceStatistics]:
    if not questions:
        return {}
    requested = {question_identity(question.prompt) for question in questions}
    pool = db.execute(select(Question.id, Question.prompt).where(Question.owner_id == owner_id)).all()
    identities = {row.id: question_identity(row.prompt) for row in pool if question_identity(row.prompt) in requested}
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    occurrences: dict[str, int] = defaultdict(int)

    def add(question_id, asset, collection, range_key):
        identity = identities[question_id]
        source_key = source_identity(asset)
        entry = grouped[identity].setdefault(source_key, {"assets": {}, "ranges": set(), "interview": False})
        entry["assets"][asset.id] = (asset, collection)
        entry["ranges"].add(range_key)
        entry["interview"] |= collection.purpose == "INTERVIEW_EXPERIENCE"

    rows = db.execute(
        select(QuestionOccurrence, Asset, Collection)
        .join(QuestionExtractionRun, QuestionExtractionRun.id == QuestionOccurrence.extraction_run_id)
        .join(AssetVersion, AssetVersion.id == QuestionOccurrence.asset_version_id)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .join(Collection, Collection.id == Asset.collection_id)
        .where(
            QuestionOccurrence.question_id.in_(identities),
            QuestionOccurrence.review_status != "REJECTED",
            QuestionExtractionRun.owner_id == owner_id, Asset.owner_id == owner_id,
            Collection.owner_id == owner_id, Asset.kind == "LIBRARY",
            AssetVersion.version == Asset.current_version,
        )
    ).all()
    for occurrence, asset, collection in rows:
        add(occurrence.question_id, asset, collection, (asset.id, occurrence.source_chunk_id, occurrence.question_start, occurrence.question_end))

    # Manual questions created from a source block also have a real provenance.
    manual_rows = db.execute(
        select(Question, Asset, Collection)
        .join(TextChunk, TextChunk.id == Question.source_chunk_id)
        .join(AssetVersion, AssetVersion.id == TextChunk.asset_version_id)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .join(Collection, Collection.id == Asset.collection_id)
        .where(Question.id.in_(identities), Question.source_type == "SOURCE",
               Asset.owner_id == owner_id, Collection.owner_id == owner_id,
               Asset.kind == "LIBRARY", AssetVersion.version == Asset.current_version)
    ).all()
    for question, asset, collection in manual_rows:
        add(question.id, asset, collection, (asset.id, question.source_chunk_id, question.id))

    today = local_today()
    summaries = {}
    for identity in requested:
        sources = []
        recent = 0
        for source_key, entry in sorted(grouped[identity].items()):
            values = sorted(entry["assets"].values(), key=lambda pair: str(pair[0].id))
            # Prefer a dated record, then an interview occurrence of this source.
            asset, collection = max(values, key=lambda pair: (pair[0].source_date is not None, pair[1].purpose == "INTERVIEW_EXPERIENCE", str(pair[0].id)))
            dates = [item.source_date for item, _ in values if item.source_date is not None]
            # Conflicting duplicate-source dates remain unknown until corrected.
            source_date = dates[0] if dates and len(set(dates)) == 1 else None
            purpose = "INTERVIEW_EXPERIENCE" if entry["interview"] else "KNOWLEDGE_BASE"
            if purpose == "INTERVIEW_EXPERIENCE" and source_date and today - timedelta(days=180) <= source_date <= today:
                recent += 1
            sources.append(QuestionSourceView(asset_id=asset.id, title=asset.title, source_url=asset.source_url,
                purpose=purpose, source_date=source_date, duplicate_asset_count=len(values)))
            occurrences[identity] += len(entry["ranges"])
        summaries[identity] = SourceStatistics(
            source_count=len(sources), interview_source_count=sum(item.purpose == "INTERVIEW_EXPERIENCE" for item in sources),
            knowledge_source_count=sum(item.purpose == "KNOWLEDGE_BASE" for item in sources), occurrence_count=occurrences[identity],
            recent_interview_source_count=recent, unknown_date_source_count=sum(item.source_date is None for item in sources),
            latest_source_date=max((item.source_date for item in sources if item.source_date), default=None), sources=sources)
    return {question.id: summaries[question_identity(question.prompt)] for question in questions}

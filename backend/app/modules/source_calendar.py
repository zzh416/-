"""Use one configured calendar for source-date validation and recency counts."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import settings


def local_today() -> date:
    return datetime.now(ZoneInfo(settings.app_timezone)).date()

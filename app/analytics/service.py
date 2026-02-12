"""Analytics service facade."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import log_event as _db_log_event
from app.leads import Lead


def log_event(
    event: str,
    *,
    user_id: int | None = None,
    lead_id: str | None = None,
    match_level: str | None = None,
    score: int | None = None,
    plan: str | None = None,
    meta: dict[str, Any] | None = None,
    ts: str | None = None,
) -> None:
    _db_log_event(
        event=event,
        user_id=user_id,
        lead_id=lead_id,
        match_level=match_level,
        score=score,
        plan=plan,
        meta=meta,
        ts=ts,
    )


def lead_id_from_lead(lead: Lead) -> str:
    payload = f"{lead.title}\n{lead.description[:600]}\n{lead.source}\n{lead.url or ''}"
    normalized = " ".join(payload.lower().split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def day_window_utc(now: datetime | None = None) -> tuple[str, str]:
    current = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def rolling_days_window_utc(days: int, now: datetime | None = None) -> tuple[str, str]:
    safe_days = max(1, int(days))
    current = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
    start = current - timedelta(days=safe_days)
    return start.isoformat(), current.isoformat()

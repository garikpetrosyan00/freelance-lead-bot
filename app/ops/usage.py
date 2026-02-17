"""Usage summary helpers for UI screens."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import suppress
from datetime import date, timedelta
from typing import Any

from app.gating import effective_cap

logger = logging.getLogger(__name__)


def _empty_summary() -> dict[str, Any]:
    return {
        "today_sent": None,
        "today_blocked": None,
        "week_sent": None,
        "week_blocked": None,
        "daily_limit": None,
        "plan": None,
        "min_level": None,
    }


def _pick_event_name(rows: list[tuple[str, int]], keyword: str, preferred: str) -> str | None:
    names = [str(name or "").strip().lower() for name, _ in rows if str(name or "").strip()]
    if preferred in names:
        return preferred

    candidates = [name for name in names if "lead" in name and keyword in name]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _count_event_for_day(conn: sqlite3.Connection, user_id: int, event_name: str, day_iso: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM analytics_events
        WHERE user_id = ?
          AND event = ?
          AND substr(ts, 1, 10) = ?
        """,
        (user_id, event_name, day_iso),
    ).fetchone()
    return int(row[0]) if row else 0


def _count_event_for_range(
    conn: sqlite3.Connection,
    user_id: int,
    event_name: str,
    start_day_iso: str,
    end_day_iso: str,
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM analytics_events
        WHERE user_id = ?
          AND event = ?
          AND substr(ts, 1, 10) >= ?
          AND substr(ts, 1, 10) <= ?
        """,
        (user_id, event_name, start_day_iso, end_day_iso),
    ).fetchone()
    return int(row[0]) if row else 0


def _load_event_names(
    conn: sqlite3.Connection,
    user_id: int,
    start_day_iso: str,
    end_day_iso: str,
) -> list[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT event, COUNT(*) as c
        FROM analytics_events
        WHERE user_id = ?
          AND substr(ts, 1, 10) >= ?
          AND substr(ts, 1, 10) <= ?
        GROUP BY event
        ORDER BY c DESC
        LIMIT 100
        """,
        (user_id, start_day_iso, end_day_iso),
    ).fetchall()
    return [(str(row[0]), int(row[1])) for row in rows if row and row[0]]


async def get_usage_summary(db, user_id: int) -> dict[str, Any]:
    summary = _empty_summary()

    try:
        plan = db.get_plan(user_id)
        min_level, _ = db.get_user_settings(user_id)
        summary["plan"] = plan
        summary["min_level"] = min_level
        summary["daily_limit"] = effective_cap(plan, None)
    except Exception as exc:
        logger.warning("Failed to read plan/settings for usage summary", exc_info=True)
        with suppress(Exception):
            db.record_error("ui", exc, context={"action": "usage_plan_settings", "user_id": user_id})

    conn: sqlite3.Connection | None = None
    try:
        db_path = getattr(db, "DB_PATH", None)
        db_uri = bool(getattr(db, "DB_URI", False))
        if not db_path:
            return summary

        conn = sqlite3.connect(db_path, uri=db_uri)
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='analytics_events' LIMIT 1"
        ).fetchone()
        if not row:
            return summary

        today = date.today()
        today_iso = today.isoformat()
        week_start_iso = (today - timedelta(days=6)).isoformat()

        event_rows = _load_event_names(conn, user_id, week_start_iso, today_iso)
        sent_event = _pick_event_name(event_rows, keyword="sent", preferred="lead_sent")
        blocked_event = _pick_event_name(event_rows, keyword="blocked", preferred="lead_blocked")

        if sent_event:
            summary["today_sent"] = _count_event_for_day(conn, user_id, sent_event, today_iso)
            summary["week_sent"] = _count_event_for_range(
                conn,
                user_id,
                sent_event,
                week_start_iso,
                today_iso,
            )
        if blocked_event:
            summary["today_blocked"] = _count_event_for_day(conn, user_id, blocked_event, today_iso)
            summary["week_blocked"] = _count_event_for_range(
                conn,
                user_id,
                blocked_event,
                week_start_iso,
                today_iso,
            )
    except Exception as exc:
        logger.warning("Failed to read analytics usage summary", exc_info=True)
        with suppress(Exception):
            db.record_error("ui", exc, context={"action": "usage_analytics", "user_id": user_id})
    finally:
        if conn is not None:
            with suppress(Exception):
                conn.close()

    return summary

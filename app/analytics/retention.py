"""Retention and subscription-health analytics helpers (SQLite-backed)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any

from app import db as db_module

logger = logging.getLogger(__name__)

_ACTIVITY_EVENTS = (
    "lead_sent",
    "lead_blocked",
    "upgrade_requested",
    "checkout_created",
    "payment_confirmed",
    "pro_activated",
    "reconcile_attempted",
)


def _connect() -> sqlite3.Connection:
    return db_module._connect()


def _parse_iso_utc(iso_value: str) -> datetime:
    raw = iso_value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _day_range(since_iso: str, until_iso: str) -> list[date]:
    since_dt = _parse_iso_utc(since_iso)
    until_dt = _parse_iso_utc(until_iso)
    start = since_dt.date()
    end = until_dt.date()
    if until_dt.time() != datetime.min.time() or until_dt.microsecond:
        end = end + timedelta(days=1)
    if end <= start:
        return []
    days: list[date] = []
    current = start
    while current < end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _day_start_iso(day: date) -> str:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).isoformat()


def _day_end_iso(day: date) -> str:
    return _day_start_iso(day + timedelta(days=1))


def _events_placeholders(events: tuple[str, ...]) -> str:
    return ",".join("?" for _ in events)


def _count_distinct_active_users(conn: sqlite3.Connection, start_iso: str, end_iso: str) -> int:
    placeholders = _events_placeholders(_ACTIVITY_EVENTS)
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT user_id)
        FROM analytics_events
        WHERE user_id IS NOT NULL
          AND event IN ({placeholders})
          AND ts >= ?
          AND ts < ?
        """,
        (*_ACTIVITY_EVENTS, start_iso, end_iso),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _fetch_active_users(conn: sqlite3.Connection, start_iso: str, end_iso: str) -> set[int]:
    placeholders = _events_placeholders(_ACTIVITY_EVENTS)
    rows = conn.execute(
        f"""
        SELECT DISTINCT user_id
        FROM analytics_events
        WHERE user_id IS NOT NULL
          AND event IN ({placeholders})
          AND ts >= ?
          AND ts < ?
        """,
        (*_ACTIVITY_EVENTS, start_iso, end_iso),
    ).fetchall()
    result: set[int] = set()
    for row in rows:
        try:
            result.add(int(row[0]))
        except Exception:
            continue
    return result


def get_dau_series(since_iso: str, until_iso: str) -> list[dict[str, Any]]:
    try:
        days = _day_range(since_iso, until_iso)
        if not days:
            return []
        result: list[dict[str, Any]] = []
        with _connect() as conn:
            for day in days:
                count = _count_distinct_active_users(conn, _day_start_iso(day), _day_end_iso(day))
                result.append({"date": day.isoformat(), "dau": count})
        return result
    except Exception:
        logger.warning("get_dau_series failed", exc_info=True)
        return []


def get_wau_series(since_iso: str, until_iso: str) -> list[dict[str, Any]]:
    try:
        days = _day_range(since_iso, until_iso)
        if not days:
            return []
        result: list[dict[str, Any]] = []
        with _connect() as conn:
            for day in days:
                start = day - timedelta(days=6)
                count = _count_distinct_active_users(conn, _day_start_iso(start), _day_end_iso(day))
                result.append({"date": day.isoformat(), "wau": count})
        return result
    except Exception:
        logger.warning("get_wau_series failed", exc_info=True)
        return []


def get_mau_series_28d(since_iso: str, until_iso: str) -> list[dict[str, Any]]:
    try:
        days = _day_range(since_iso, until_iso)
        if not days:
            return []
        result: list[dict[str, Any]] = []
        with _connect() as conn:
            for day in days:
                start = day - timedelta(days=27)
                count = _count_distinct_active_users(conn, _day_start_iso(start), _day_end_iso(day))
                result.append({"date": day.isoformat(), "mau": count})
        return result
    except Exception:
        logger.warning("get_mau_series_28d failed", exc_info=True)
        return []


def get_retention_7d_series(since_iso: str, until_iso: str) -> list[dict[str, Any]]:
    try:
        days = _day_range(since_iso, until_iso)
        if not days:
            return []
        result: list[dict[str, Any]] = []
        with _connect() as conn:
            for day in days:
                day_users = _fetch_active_users(conn, _day_start_iso(day), _day_end_iso(day))
                prior_day = day - timedelta(days=7)
                prior_users = _fetch_active_users(
                    conn,
                    _day_start_iso(prior_day),
                    _day_end_iso(prior_day),
                )
                base = len(day_users)
                retained = len(day_users & prior_users)
                retention = (100.0 * retained / base) if base > 0 else 0.0
                result.append(
                    {
                        "date": day.isoformat(),
                        "retention_7d_pct": retention,
                        "base": base,
                        "retained": retained,
                    }
                )
        return result
    except Exception:
        logger.warning("get_retention_7d_series failed", exc_info=True)
        return []


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _active_pro_now(conn: sqlite3.Connection) -> int:
    if _table_exists(conn, "users"):
        cols = _table_columns(conn, "users")
        if "plan" in cols:
            row = conn.execute(
                "SELECT COUNT(*) FROM users WHERE UPPER(COALESCE(plan, 'FREE')) = 'PRO'"
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    if _table_exists(conn, "user_plan"):
        row = conn.execute(
            "SELECT COUNT(*) FROM user_plan WHERE UPPER(COALESCE(plan, 'FREE')) = 'PRO'"
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT u.user_id,
                   (
                       SELECT e2.event
                       FROM analytics_events e2
                       WHERE e2.user_id = u.user_id
                         AND e2.event IN ('pro_activated', 'pro_downgraded')
                       ORDER BY e2.ts DESC, e2.id DESC
                       LIMIT 1
                   ) AS last_event
            FROM (
                SELECT DISTINCT user_id
                FROM analytics_events
                WHERE user_id IS NOT NULL
                  AND event IN ('pro_activated', 'pro_downgraded')
            ) u
        )
        WHERE last_event = 'pro_activated'
        """
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _event_count(conn: sqlite3.Connection, event: str, since_iso: str, until_iso: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM analytics_events
        WHERE event = ?
          AND ts >= ?
          AND ts < ?
        """,
        (event, since_iso, until_iso),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return 100.0 * float(numerator) / float(denominator)


def _activation_minutes_by_user(
    conn: sqlite3.Connection,
    since_iso: str,
    until_iso: str,
) -> list[float]:
    rows = conn.execute(
        """
        SELECT c.user_id,
               MIN((julianday(a.ts) - julianday(c.ts)) * 24.0 * 60.0) AS minutes_to_activate
        FROM analytics_events c
        JOIN analytics_events a
          ON a.user_id = c.user_id
         AND a.event = 'pro_activated'
         AND a.ts >= c.ts
         AND a.ts < ?
        WHERE c.event = 'checkout_created'
          AND c.user_id IS NOT NULL
          AND c.ts >= ?
          AND c.ts < ?
        GROUP BY c.user_id
        HAVING minutes_to_activate IS NOT NULL
        """,
        (until_iso, since_iso, until_iso),
    ).fetchall()
    values: list[float] = []
    for row in rows:
        try:
            minutes = float(row[1])
        except Exception:
            continue
        if minutes >= 0:
            values.append(minutes)
    return values


def get_pro_health(since_iso: str, until_iso: str) -> dict[str, Any]:
    safe_default: dict[str, Any] = {
        "since": since_iso,
        "until": until_iso,
        "pro_activated": 0,
        "pro_downgraded": 0,
        "subscription_canceled": 0,
        "subscription_deleted": 0,
        "checkout_created": 0,
        "payment_confirmed": 0,
        "upgrade_requested": 0,
        "net_change": 0,
        "active_pro_now": 0,
        "conversions": {
            "checkout_to_paid_pct": 0.0,
            "paid_to_activated_pct": 0.0,
            "upgrade_requested_to_activated_pct": 0.0,
        },
        "time_to_activate": {
            "users": 0,
            "avg_minutes": None,
            "median_minutes": None,
        },
    }

    try:
        with _connect() as conn:
            activated = _event_count(conn, "pro_activated", since_iso, until_iso)
            downgraded = _event_count(conn, "pro_downgraded", since_iso, until_iso)
            subscription_canceled = _event_count(conn, "subscription_canceled", since_iso, until_iso)
            subscription_deleted = _event_count(conn, "subscription_deleted", since_iso, until_iso)
            checkout_created = _event_count(conn, "checkout_created", since_iso, until_iso)
            payment_confirmed = _event_count(conn, "payment_confirmed", since_iso, until_iso)
            upgrade_requested = _event_count(conn, "upgrade_requested", since_iso, until_iso)

            minutes = _activation_minutes_by_user(conn, since_iso, until_iso)
            avg_minutes = (sum(minutes) / len(minutes)) if minutes else None
            median_minutes = median(minutes) if minutes else None

            return {
                "since": since_iso,
                "until": until_iso,
                "pro_activated": activated,
                "pro_downgraded": downgraded,
                "subscription_canceled": subscription_canceled,
                "subscription_deleted": subscription_deleted,
                "checkout_created": checkout_created,
                "payment_confirmed": payment_confirmed,
                "upgrade_requested": upgrade_requested,
                "net_change": activated - downgraded,
                "active_pro_now": _active_pro_now(conn),
                "conversions": {
                    "checkout_to_paid_pct": _pct(payment_confirmed, checkout_created),
                    "paid_to_activated_pct": _pct(activated, payment_confirmed),
                    "upgrade_requested_to_activated_pct": _pct(activated, upgrade_requested),
                },
                "time_to_activate": {
                    "users": len(minutes),
                    "avg_minutes": avg_minutes,
                    "median_minutes": median_minutes,
                },
            }
    except Exception:
        logger.warning("get_pro_health failed", exc_info=True)
        return safe_default

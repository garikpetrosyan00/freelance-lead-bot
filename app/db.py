"""SQLite storage for user preferences."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, List

DB_PATH = os.path.join("data", "app.db")


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_prefs (
                user_id INTEGER PRIMARY KEY,
                skills TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                is_active INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_plan (
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                notifications_sent INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, day)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notify_state (
                user_id INTEGER PRIMARY KEY,
                last_sent_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_seen (
                user_id INTEGER NOT NULL,
                lead_hash TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (user_id, lead_hash)
            )
            """
        )
        conn.commit()


def _normalize_skills(raw_skills: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for skill in raw_skills:
        normalized = skill.strip().lower()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def set_skills(user_id: int, skills: list[str]) -> None:
    normalized = _normalize_skills(skills)
    skills_str = ", ".join(normalized)
    timestamp = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_prefs (user_id, skills, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                skills=excluded.skills,
                updated_at=excluded.updated_at
            """,
            (user_id, skills_str, timestamp),
        )
        conn.commit()


def get_skills(user_id: int) -> list[str]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT skills FROM user_prefs WHERE user_id = ?", (user_id,)
        ).fetchone()

    if not row:
        return []

    skills_str = row[0] or ""
    return [skill.strip() for skill in skills_str.split(",") if skill.strip()]


def set_subscription(user_id: int, is_active: bool) -> None:
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (user_id, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                is_active=excluded.is_active,
                updated_at=excluded.updated_at
            """,
            (user_id, int(is_active), now, now),
        )
        conn.commit()


def is_subscribed(user_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT is_active FROM subscriptions WHERE user_id = ?", (user_id,)
        ).fetchone()

    if not row:
        return False

    return bool(row[0])


def list_subscribed_users() -> list[int]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id FROM subscriptions WHERE is_active = 1"
        ).fetchall()

    return [int(row[0]) for row in rows]


def get_plan(user_id: int) -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT plan FROM user_plan WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return "FREE"
    plan = str(row[0]).upper().strip()
    return plan or "FREE"


def set_plan(user_id: int, plan: str) -> None:
    plan = plan.upper().strip()
    if plan not in {"FREE", "PRO"}:
        raise ValueError("plan must be FREE or PRO")
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_plan (user_id, plan, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                plan=excluded.plan,
                updated_at=excluded.updated_at
            """,
            (user_id, plan, now, now),
        )
        conn.commit()


def get_daily_usage(user_id: int, day: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT notifications_sent FROM daily_usage WHERE user_id = ? AND day = ?",
            (user_id, day),
        ).fetchone()
    if not row:
        return 0
    return int(row[0])


def increment_daily_usage(user_id: int, day: str, by: int = 1) -> int:
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_usage (user_id, day, notifications_sent, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, day) DO UPDATE SET
                notifications_sent=daily_usage.notifications_sent + excluded.notifications_sent,
                updated_at=excluded.updated_at
            """,
            (user_id, day, int(by), now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT notifications_sent FROM daily_usage WHERE user_id = ? AND day = ?",
            (user_id, day),
        ).fetchone()
    return int(row[0]) if row else 0


def get_last_sent_at(user_id: int) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_sent_at FROM notify_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return str(row[0])


def set_last_sent_at(user_id: int, ts_iso: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO notify_state (user_id, last_sent_at)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                last_sent_at=excluded.last_sent_at
            """,
            (user_id, ts_iso),
        )
        conn.commit()


def has_seen_lead(user_id: int, lead_hash: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM lead_seen WHERE user_id = ? AND lead_hash = ?",
            (user_id, lead_hash),
        ).fetchone()
    return row is not None


def mark_seen_lead(user_id: int, lead_hash: str) -> None:
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO lead_seen (user_id, lead_hash, first_seen_at)
            VALUES (?, ?, ?)
            """,
            (user_id, lead_hash, now),
        )
        conn.commit()

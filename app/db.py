"""SQLite storage for user preferences."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, List

DB_PATH = os.path.join("data", "app.db")
DB_URI = False


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, uri=DB_URI)


def configure_db(path: str, uri: bool | None = None) -> None:
    global DB_PATH, DB_URI
    DB_PATH = path
    if uri is None:
        DB_URI = path.startswith("file:")
    else:
        DB_URI = bool(uri)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def init_db() -> None:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not DB_URI and DB_PATH != ":memory:":
        os.makedirs(db_dir, exist_ok=True)
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                min_level TEXT NOT NULL,
                daily_cap INTEGER,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS upgrade_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                admin_id INTEGER,
                admin_note TEXT,
                decided_at TEXT,
                user_settings_json TEXT,
                paid INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_upgrade_requests_status_created_at
            ON upgrade_requests(status, created_at)
            """
        )
        try:
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_upgrade_requests_unique_pending
                ON upgrade_requests(user_id)
                WHERE status = 'pending'
                """
            )
        except sqlite3.OperationalError:
            # Older SQLite builds may not support partial indexes.
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_upgrade_requests_user_status
                ON upgrade_requests(user_id, status)
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


def get_user_plan(user_id: int) -> str:
    return get_plan(user_id)


def mark_user_pro(
    user_id: int,
    enabled: bool,
    activated_at_iso: str,
    plan: str = "PRO",
) -> None:
    target_plan = plan.upper().strip() if enabled else "FREE"
    if target_plan not in {"FREE", "PRO"}:
        raise ValueError("plan must be FREE or PRO")

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_plan (user_id, plan, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                plan=excluded.plan,
                updated_at=excluded.updated_at
            """,
            (user_id, target_plan, activated_at_iso, activated_at_iso),
        )
        conn.commit()


def _upgrade_request_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "user_id": int(row[1]),
        "username": row[2],
        "created_at": str(row[3]),
        "status": str(row[4]),
        "admin_id": int(row[5]) if row[5] is not None else None,
        "admin_note": row[6],
        "decided_at": row[7],
        "user_settings_json": row[8],
        "paid": int(row[9]),
    }


def create_upgrade_request(
    user_id: int,
    username: str | None,
    settings_snapshot_json: str | None,
    paid: int = 0,
) -> int:
    request_id, _ = create_upgrade_request_with_state(
        user_id=user_id,
        username=username,
        settings_snapshot_json=settings_snapshot_json,
        paid=paid,
    )
    return request_id


def create_upgrade_request_with_state(
    user_id: int,
    username: str | None,
    settings_snapshot_json: str | None,
    paid: int = 0,
) -> tuple[int, bool]:
    with _connect() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT id
                FROM upgrade_requests
                WHERE user_id = ? AND status = 'pending'
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if existing:
                conn.commit()
                return int(existing[0]), False

            now = _utc_now()
            conn.execute(
                """
                INSERT INTO upgrade_requests (
                    user_id,
                    username,
                    created_at,
                    status,
                    admin_id,
                    admin_note,
                    decided_at,
                    user_settings_json,
                    paid
                )
                VALUES (?, ?, ?, 'pending', NULL, NULL, NULL, ?, ?)
                """,
                (user_id, username, now, settings_snapshot_json, int(bool(paid))),
            )
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            conn.commit()
            return (int(row[0]) if row else 0), True
        except sqlite3.IntegrityError:
            conn.rollback()
            # Handles race with unique pending index.
            existing = conn.execute(
                """
                SELECT id
                FROM upgrade_requests
                WHERE user_id = ? AND status = 'pending'
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if existing:
                return int(existing[0]), False
            raise


def get_pending_upgrade_request_id(user_id: int) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM upgrade_requests
            WHERE user_id = ? AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return int(row[0])


def list_pending_upgrade_requests(limit: int = 50) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                user_id,
                username,
                created_at,
                status,
                admin_id,
                admin_note,
                decided_at,
                user_settings_json,
                paid
            FROM upgrade_requests
            WHERE status = 'pending'
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [_upgrade_request_row_to_dict(row) for row in rows]


def get_upgrade_request_by_id(request_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                user_id,
                username,
                created_at,
                status,
                admin_id,
                admin_note,
                decided_at,
                user_settings_json,
                paid
            FROM upgrade_requests
            WHERE id = ?
            """,
            (request_id,),
        ).fetchone()
    if not row:
        return None
    return _upgrade_request_row_to_dict(row)


def set_upgrade_request_status(
    request_id: int,
    status: str,
    admin_id: int,
    admin_note: str | None,
    decided_at_iso: str,
) -> None:
    normalized = status.lower().strip()
    if normalized not in {"pending", "approved", "rejected"}:
        raise ValueError("status must be pending, approved, or rejected")

    with _connect() as conn:
        conn.execute(
            """
            UPDATE upgrade_requests
            SET
                status = ?,
                admin_id = ?,
                admin_note = ?,
                decided_at = ?
            WHERE id = ?
            """,
            (normalized, admin_id, admin_note, decided_at_iso, request_id),
        )
        conn.commit()


def decide_upgrade_request(
    request_id: int,
    new_status: str,
    admin_id: int,
    admin_note: str | None,
    decided_at_iso: str,
) -> bool:
    normalized = new_status.lower().strip()
    if normalized not in {"approved", "rejected"}:
        raise ValueError("new_status must be approved or rejected")

    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE upgrade_requests
            SET
                status = ?,
                admin_id = ?,
                admin_note = ?,
                decided_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (normalized, admin_id, admin_note, decided_at_iso, request_id),
        )
        conn.commit()
        return int(cursor.rowcount) > 0


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


def get_user_settings(user_id: int) -> tuple[str, int | None]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT min_level, daily_cap FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return "MEDIUM", None
    min_level = str(row[0]).upper().strip() if row[0] else "MEDIUM"
    daily_cap = row[1]
    return min_level or "MEDIUM", int(daily_cap) if daily_cap is not None else None


def set_user_min_level(user_id: int, min_level: str) -> None:
    min_level = min_level.upper().strip()
    if min_level not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("min_level must be LOW, MEDIUM, or HIGH")
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, min_level, daily_cap, updated_at)
            VALUES (?, ?, NULL, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                min_level=excluded.min_level,
                daily_cap=user_settings.daily_cap,
                updated_at=excluded.updated_at
            """,
            (user_id, min_level, now),
        )
        conn.commit()


def set_user_daily_cap(user_id: int, daily_cap: int | None) -> None:
    if daily_cap is not None and daily_cap <= 0:
        raise ValueError("daily_cap must be a positive integer or None")
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, min_level, daily_cap, updated_at)
            VALUES (?, 'MEDIUM', ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                min_level=user_settings.min_level,
                daily_cap=excluded.daily_cap,
                updated_at=excluded.updated_at
            """,
            (user_id, daily_cap, now),
        )
        conn.commit()

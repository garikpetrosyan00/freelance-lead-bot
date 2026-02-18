"""SQLite storage for user preferences."""

from __future__ import annotations

import os
import sqlite3
import json
import logging
import time
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List

from app.ops.logging_utils import safe_exc, sanitize_meta

DB_PATH = os.path.join("data", "app.db")
DB_URI = False
logger = logging.getLogger(__name__)
_JSON_EXTRACT_SUPPORTED: bool | None = None

# SQLite reliability defaults (WAL-oriented and safe for most bot workloads).
SQLITE_PRAGMA_FOREIGN_KEYS = "ON"
SQLITE_PRAGMA_JOURNAL_MODE = "WAL"
SQLITE_PRAGMA_SYNCHRONOUS = "NORMAL"
SQLITE_PRAGMA_BUSY_TIMEOUT_MS = 5000
SQLITE_PRAGMA_TEMP_STORE = "MEMORY"
SQLITE_PRAGMA_WAL_AUTOCHECKPOINT = 1000
SQLITE_PRAGMA_JOURNAL_SIZE_LIMIT = 67_108_864
STARTUP_BUSY_RETRIES = 3
STARTUP_BUSY_SLEEP_SECONDS = 0.5
ERROR_EVENT_MESSAGE_MAX_LEN = 300
MAX_ERROR_CONTEXT_BYTES = 8192
ERROR_DEDUPE_WINDOW_SECONDS = 300
DEFAULT_MIN_SKILL_MATCHES = 3
LEGACY_MIN_LEVEL_TO_SKILL_MATCHES = {
    "LOW": 3,
    "MEDIUM": 4,
    "HIGH": 5,
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, uri=DB_URI)
    _apply_sqlite_pragmas(conn)
    return conn


def configure_db(path: str, uri: bool | None = None) -> None:
    global DB_PATH, DB_URI
    DB_PATH = path
    if uri is None:
        DB_URI = path.startswith("file:")
    else:
        DB_URI = bool(uri)


def _apply_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute(f"PRAGMA foreign_keys={SQLITE_PRAGMA_FOREIGN_KEYS}")
    conn.execute(f"PRAGMA journal_mode={SQLITE_PRAGMA_JOURNAL_MODE}")
    conn.execute(f"PRAGMA synchronous={SQLITE_PRAGMA_SYNCHRONOUS}")
    conn.execute(f"PRAGMA busy_timeout={int(SQLITE_PRAGMA_BUSY_TIMEOUT_MS)}")
    conn.execute(f"PRAGMA temp_store={SQLITE_PRAGMA_TEMP_STORE}")
    conn.execute(f"PRAGMA wal_autocheckpoint={int(SQLITE_PRAGMA_WAL_AUTOCHECKPOINT)}")
    conn.execute(f"PRAGMA journal_size_limit={int(SQLITE_PRAGMA_JOURNAL_SIZE_LIMIT)}")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    for row in rows:
        if len(row) > 1 and str(row[1]) == column_name:
            return True
    return False


def run_startup_sanity_checks(required_tables: Iterable[str]) -> None:
    if not DB_URI and DB_PATH != ":memory:":
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        with open(DB_PATH, "a", encoding="utf-8"):
            pass

    with _connect() as conn:
        quick_check_rows = conn.execute("PRAGMA quick_check").fetchall()
        quick_check_values = [str(row[0]).strip().lower() for row in quick_check_rows if row]
        if not quick_check_values or any(value != "ok" for value in quick_check_values):
            raise RuntimeError(f"PRAGMA quick_check failed: {quick_check_values or ['<empty>']}")
        conn.execute("SELECT 1").fetchone()

    for attempt in range(1, STARTUP_BUSY_RETRIES + 1):
        try:
            with _connect() as conn:
                conn.execute("CREATE TEMP TABLE __startup_test(x INTEGER)")
                conn.execute("DROP TABLE __startup_test")
            break
        except sqlite3.OperationalError as exc:
            sqlite_code = getattr(exc, "sqlite_errorcode", None)
            is_busy = sqlite_code == sqlite3.SQLITE_BUSY or "busy" in str(exc).lower()
            if not is_busy:
                raise
            if attempt >= STARTUP_BUSY_RETRIES:
                logger.warning(
                    "DB startup write probe is still busy after %d attempts; continuing startup",
                    STARTUP_BUSY_RETRIES,
                    exc_info=True,
                )
                break
            time.sleep(STARTUP_BUSY_SLEEP_SECONDS)

    init_db()

    with _connect() as conn:
        missing = [table for table in required_tables if not _table_exists(conn, table)]
    if missing:
        raise RuntimeError(f"missing required DB tables: {', '.join(sorted(missing))}")


def checkpoint_wal_passive() -> None:
    try:
        with _connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception:
        logger.warning("WAL passive checkpoint failed during shutdown", exc_info=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
        if not _column_exists(conn, "user_plan", "expires_at"):
            conn.execute("ALTER TABLE user_plan ADD COLUMN expires_at TEXT")
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
                min_skill_matches INTEGER,
                daily_cap INTEGER,
                updated_at TEXT NOT NULL
            )
            """
        )
        if not _column_exists(conn, "user_settings", "min_skill_matches"):
            conn.execute("ALTER TABLE user_settings ADD COLUMN min_skill_matches INTEGER")
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
            CREATE TABLE IF NOT EXISTS processed_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                request_id INTEGER,
                checkout_session_id TEXT UNIQUE,
                payment_intent_id TEXT,
                subscription_id TEXT,
                customer_id TEXT,
                amount_total INTEGER,
                currency TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stripe_subscriptions (
                user_id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                subscription_id TEXT UNIQUE,
                customer_id TEXT,
                status TEXT NOT NULL,
                current_period_end TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                event TEXT NOT NULL,
                user_id INTEGER,
                lead_id TEXT,
                match_level TEXT,
                score INTEGER,
                plan TEXT,
                meta_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monitor_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monitor_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                meta_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS error_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                component TEXT NOT NULL,
                error_type TEXT NOT NULL,
                message TEXT NOT NULL,
                context_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_upgrade_requests_status_created_at
            ON upgrade_requests(status, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payments_user_status
            ON payments(user_id, status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payments_subscription_id
            ON payments(subscription_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payments_checkout_session_id
            ON payments(checkout_session_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ae_ts
            ON analytics_events(ts)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ae_event_ts
            ON analytics_events(event, ts)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ae_user_ts
            ON analytics_events(user_id, ts)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ae_lead
            ON analytics_events(lead_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_monitor_alerts_ts
            ON monitor_alerts(ts)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_monitor_alerts_type_ts
            ON monitor_alerts(alert_type, ts)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_error_events_ts
            ON error_events(ts)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_error_events_component_ts
            ON error_events(component, ts)
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

    expires_at: str | None = None
    if enabled:
        activated_at = _parse_iso_utc(activated_at_iso)
        if activated_at is None:
            activated_at = datetime.now(timezone.utc)
        expires_at = (activated_at + timedelta(days=30)).isoformat()

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_plan (user_id, plan, expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                plan=excluded.plan,
                expires_at=excluded.expires_at,
                updated_at=excluded.updated_at
            """,
            (user_id, target_plan, expires_at, activated_at_iso, activated_at_iso),
        )
        conn.commit()


def get_user_plan_with_expiry(user_id: int) -> tuple[str, str | None]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT plan, expires_at FROM user_plan WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return "FREE", None
    plan = str(row[0]).upper().strip() or "FREE"
    expires_at = str(row[1]).strip() if row[1] else None
    return plan, expires_at


def expire_overdue_pro_users(now_iso: str) -> list[int]:
    now_dt = _parse_iso_utc(now_iso) or datetime.now(timezone.utc)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT user_id
            FROM user_plan
            WHERE plan = 'PRO'
              AND expires_at IS NOT NULL
              AND expires_at < ?
            """,
            (now_dt.isoformat(),),
        ).fetchall()
        user_ids = [int(row[0]) for row in rows]
        if not user_ids:
            return []
        conn.execute(
            """
            UPDATE user_plan
            SET plan = 'FREE',
                expires_at = NULL,
                updated_at = ?
            WHERE plan = 'PRO'
              AND expires_at IS NOT NULL
              AND expires_at < ?
            """,
            (now_dt.isoformat(), now_dt.isoformat()),
        )
        conn.commit()
    return user_ids


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


def attach_payment_to_upgrade_request(request_id: int, paid: int = 1) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE upgrade_requests
            SET paid = ?
            WHERE id = ?
            """,
            (int(bool(paid)), request_id),
        )
        conn.commit()
        return int(cursor.rowcount) > 0


def mark_event_processed(provider: str, event_id: str) -> bool:
    now = _utc_now()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO processed_events (provider, event_id, created_at)
                VALUES (?, ?, ?)
                """,
                (provider, event_id, now),
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False


def unmark_event_processed(provider: str, event_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM processed_events WHERE provider = ? AND event_id = ?",
            (provider, event_id),
        )
        conn.commit()


def upsert_payment_from_checkout(session_obj: dict[str, Any]) -> None:
    checkout_session_id = str(session_obj.get("id") or "").strip()
    if not checkout_session_id:
        raise ValueError("checkout session id is required")

    metadata = session_obj.get("metadata") or {}
    user_id_raw = metadata.get("user_id")
    if user_id_raw is None:
        raise ValueError("metadata.user_id is required")
    user_id = int(user_id_raw)

    request_id_raw = metadata.get("request_id")
    request_id = int(request_id_raw) if str(request_id_raw or "").isdigit() else None

    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO payments (
                provider,
                user_id,
                request_id,
                checkout_session_id,
                payment_intent_id,
                subscription_id,
                customer_id,
                amount_total,
                currency,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)
            ON CONFLICT(checkout_session_id) DO UPDATE SET
                user_id=excluded.user_id,
                request_id=COALESCE(excluded.request_id, payments.request_id),
                payment_intent_id=COALESCE(excluded.payment_intent_id, payments.payment_intent_id),
                subscription_id=COALESCE(excluded.subscription_id, payments.subscription_id),
                customer_id=COALESCE(excluded.customer_id, payments.customer_id),
                amount_total=COALESCE(excluded.amount_total, payments.amount_total),
                currency=COALESCE(excluded.currency, payments.currency),
                updated_at=excluded.updated_at
            """,
            (
                "stripe",
                user_id,
                request_id,
                checkout_session_id,
                str(session_obj.get("payment_intent") or "") or None,
                str(session_obj.get("subscription") or "") or None,
                str(session_obj.get("customer") or "") or None,
                int(session_obj.get("amount_total")) if session_obj.get("amount_total") is not None else None,
                str(session_obj.get("currency") or "") or None,
                now,
                now,
            ),
        )
        conn.commit()


def mark_payment_paid_by_session(
    session_id: str,
    *,
    payment_intent_id: str | None = None,
    subscription_id: str | None = None,
    customer_id: str | None = None,
    amount_total: int | None = None,
    currency: str | None = None,
) -> bool:
    now = _utc_now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET
                payment_intent_id = COALESCE(?, payment_intent_id),
                subscription_id = COALESCE(?, subscription_id),
                customer_id = COALESCE(?, customer_id),
                amount_total = COALESCE(?, amount_total),
                currency = COALESCE(?, currency),
                status = 'paid',
                updated_at = ?
            WHERE checkout_session_id = ?
            """,
            (
                payment_intent_id,
                subscription_id,
                customer_id,
                amount_total,
                currency,
                now,
                session_id,
            ),
        )
        conn.commit()
        return int(cursor.rowcount) > 0


def mark_payment_paid_by_subscription_id(
    subscription_id: str,
    *,
    amount_total: int | None = None,
    currency: str | None = None,
    status: str = "paid",
) -> bool:
    now = _utc_now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET
                amount_total = COALESCE(?, amount_total),
                currency = COALESCE(?, currency),
                status = ?,
                updated_at = ?
            WHERE subscription_id = ?
            """,
            (amount_total, currency, status, now, subscription_id),
        )
        conn.commit()
        return int(cursor.rowcount) > 0


def update_payment_status_by_subscription_id(subscription_id: str, status: str) -> bool:
    now = _utc_now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE payments
            SET status = ?, updated_at = ?
            WHERE subscription_id = ?
            """,
            (status, now, subscription_id),
        )
        conn.commit()
        return int(cursor.rowcount) > 0


def get_payment_user_id_by_subscription_id(subscription_id: str) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM payments WHERE subscription_id = ? ORDER BY id DESC LIMIT 1",
            (subscription_id,),
        ).fetchone()
    if not row:
        return None
    return int(row[0])


def upsert_stripe_subscription(
    user_id: int,
    subscription_id: str,
    customer_id: str | None,
    status: str,
    current_period_end: str | None,
) -> None:
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO stripe_subscriptions (
                user_id,
                provider,
                subscription_id,
                customer_id,
                status,
                current_period_end,
                updated_at
            )
            VALUES (?, 'stripe', ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                subscription_id=excluded.subscription_id,
                customer_id=COALESCE(excluded.customer_id, stripe_subscriptions.customer_id),
                status=excluded.status,
                current_period_end=excluded.current_period_end,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                subscription_id,
                customer_id,
                status,
                current_period_end,
                now,
            ),
        )
        conn.commit()


def get_subscription_user_id(subscription_id: str) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT user_id
            FROM stripe_subscriptions
            WHERE subscription_id = ?
            LIMIT 1
            """,
            (subscription_id,),
        ).fetchone()
    if not row:
        return None
    return int(row[0])


def _payment_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "provider": str(row[1]),
        "user_id": int(row[2]),
        "request_id": int(row[3]) if row[3] is not None else None,
        "checkout_session_id": row[4],
        "payment_intent_id": row[5],
        "subscription_id": row[6],
        "customer_id": row[7],
        "amount_total": int(row[8]) if row[8] is not None else None,
        "currency": row[9],
        "status": str(row[10]),
        "created_at": str(row[11]),
        "updated_at": str(row[12]),
    }


def get_latest_payment_for_user(user_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                id,
                provider,
                user_id,
                request_id,
                checkout_session_id,
                payment_intent_id,
                subscription_id,
                customer_id,
                amount_total,
                currency,
                status,
                created_at,
                updated_at
            FROM payments
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return _payment_row_to_dict(row)


def list_recent_payments(limit: int = 20) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                provider,
                user_id,
                request_id,
                checkout_session_id,
                payment_intent_id,
                subscription_id,
                customer_id,
                amount_total,
                currency,
                status,
                created_at,
                updated_at
            FROM payments
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [_payment_row_to_dict(row) for row in rows]


def get_stripe_subscription_for_user(user_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                user_id,
                provider,
                subscription_id,
                customer_id,
                status,
                current_period_end,
                updated_at
            FROM stripe_subscriptions
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "user_id": int(row[0]),
        "provider": str(row[1]),
        "subscription_id": row[2],
        "customer_id": row[3],
        "status": str(row[4]),
        "current_period_end": row[5],
        "updated_at": str(row[6]),
    }


def list_subscriptions_by_status(status: str, limit: int = 20) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    normalized = status.strip().lower()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                user_id,
                provider,
                subscription_id,
                customer_id,
                status,
                current_period_end,
                updated_at
            FROM stripe_subscriptions
            WHERE status = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (normalized, safe_limit),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "user_id": int(row[0]),
                "provider": str(row[1]),
                "subscription_id": row[2],
                "customer_id": row[3],
                "status": str(row[4]),
                "current_period_end": row[5],
                "updated_at": str(row[6]),
            }
        )
    return result


def log_event(
    event: str,
    user_id: int | None = None,
    lead_id: str | None = None,
    match_level: str | None = None,
    score: int | None = None,
    plan: str | None = None,
    meta: dict[str, Any] | None = None,
    ts: str | None = None,
) -> None:
    timestamp = ts or _utc_now()
    meta_json = (
        json.dumps(meta, ensure_ascii=True, separators=(",", ":"))
        if meta is not None
        else None
    )
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO analytics_events (
                    ts,
                    event,
                    user_id,
                    lead_id,
                    match_level,
                    score,
                    plan,
                    meta_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    event.strip(),
                    user_id,
                    lead_id,
                    match_level,
                    score,
                    plan,
                    meta_json,
                ),
            )
            conn.commit()
    except Exception:
        logger.warning("Failed to write analytics event=%s", event, exc_info=True)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def has_event_with_session(
    user_id: int,
    event: str,
    session_id: str,
    lookback_days: int = 30,
) -> bool:
    try:
        safe_user_id = int(user_id)
    except (TypeError, ValueError):
        return False
    event_name = str(event or "").strip()
    checkout_session_id = str(session_id or "").strip()
    if safe_user_id <= 0 or not event_name or not checkout_session_id:
        return False

    try:
        safe_lookback_days = max(1, int(lookback_days))
    except (TypeError, ValueError):
        safe_lookback_days = 1
    since_iso = (datetime.now(timezone.utc) - timedelta(days=safe_lookback_days)).isoformat()
    session_json = json.dumps(checkout_session_id, ensure_ascii=True, separators=(",", ":"))[1:-1]
    needle = f'"checkout_session_id":"{session_json}"'
    spaced_needle = f'"checkout_session_id": "{session_json}"'
    compact_meta_like = f"%{_escape_like(needle)}%"
    spaced_meta_like = f"%{_escape_like(spaced_needle)}%"

    try:
        with _connect() as conn:
            if _json_extract_supported():
                try:
                    row = conn.execute(
                        """
                        SELECT 1
                        FROM analytics_events
                        WHERE user_id = ?
                          AND event = ?
                          AND ts >= ?
                          AND json_extract(meta_json, '$.checkout_session_id') = ?
                        LIMIT 1
                        """,
                        (safe_user_id, event_name, since_iso, checkout_session_id),
                    ).fetchone()
                    return row is not None
                except Exception:
                    logger.warning(
                        "JSON session lookup failed, using LIKE fallback user_id=%s event=%s",
                        safe_user_id,
                        event_name,
                        exc_info=True,
                    )

            row = conn.execute(
                """
                SELECT 1
                FROM analytics_events
                WHERE user_id = ?
                  AND event = ?
                  AND ts >= ?
                  AND (
                        meta_json LIKE ? ESCAPE '\\'
                        OR meta_json LIKE ? ESCAPE '\\'
                  )
                LIMIT 1
                """,
                (
                    safe_user_id,
                    event_name,
                    since_iso,
                    compact_meta_like,
                    spaced_meta_like,
                ),
            ).fetchone()
        return row is not None
    except Exception:
        logger.warning(
            "Failed to check analytics event with session user_id=%s event=%s",
            safe_user_id,
            event_name,
            exc_info=True,
        )
        return False


def has_recent_event(user_id: int, event: str, within_minutes: int) -> bool:
    try:
        safe_user_id = int(user_id)
    except (TypeError, ValueError):
        return False
    event_name = str(event or "").strip()
    if safe_user_id <= 0 or not event_name:
        return False

    try:
        safe_within_minutes = max(1, int(within_minutes))
    except (TypeError, ValueError):
        safe_within_minutes = 1
    since_iso = (datetime.now(timezone.utc) - timedelta(minutes=safe_within_minutes)).isoformat()

    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM analytics_events
                WHERE user_id = ?
                  AND event = ?
                  AND ts >= ?
                LIMIT 1
                """,
                (safe_user_id, event_name, since_iso),
            ).fetchone()
        return row is not None
    except Exception:
        logger.warning(
            "Failed to check recent analytics event user_id=%s event=%s",
            safe_user_id,
            event_name,
            exc_info=True,
        )
        return False


def record_error(
    component: str,
    exc: BaseException,
    context: dict[str, Any] | None = None,
    ts: str | None = None,
) -> None:
    try:
        timestamp = ts or _utc_now()
        error_type = exc.__class__.__name__
        message = safe_exc(exc)
        if len(message) > ERROR_EVENT_MESSAGE_MAX_LEN:
            message = message[:ERROR_EVENT_MESSAGE_MAX_LEN]

        context_json = None
        safe_context: dict[str, Any] = {}
        if context:
            safe_context = sanitize_meta(context)
            encoded = json.dumps(safe_context, ensure_ascii=True, separators=(",", ":"))
            if len(encoded.encode("utf-8")) <= MAX_ERROR_CONTEXT_BYTES:
                context_json = encoded
            else:
                keys = [str(key) for key in safe_context.keys()]
                keep = min(len(keys), 20)
                fallback: dict[str, Any] = {}
                while True:
                    fallback = {
                        "truncated": True,
                        "keys": keys[:keep],
                        "original_size": len(encoded.encode("utf-8")),
                    }
                    fallback_encoded = json.dumps(fallback, ensure_ascii=True, separators=(",", ":"))
                    if len(fallback_encoded.encode("utf-8")) <= MAX_ERROR_CONTEXT_BYTES:
                        context_json = fallback_encoded
                        break
                    if keep == 0:
                        context_json = json.dumps(
                            {"truncated": True, "original_size": len(encoded.encode("utf-8"))},
                            ensure_ascii=True,
                            separators=(",", ":"),
                        )
                        break
                    keep -= 1

        component_name = (component or "unknown").strip() or "unknown"
        normalized_message = " ".join(message.strip().lower().split())[:120]
        dedupe_key = f"{component_name}:{error_type}:{normalized_message}"
        dedupe_hash = hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()
        state_key = f"error:last_seen:{dedupe_hash}"
        current_dt = _parse_iso_utc(timestamp) or datetime.now(timezone.utc)

        with _connect() as conn:
            existing = conn.execute(
                "SELECT value FROM monitor_state WHERE key = ?",
                (state_key,),
            ).fetchone()
            if existing and existing[0]:
                last_seen_dt = _parse_iso_utc(str(existing[0]))
                if last_seen_dt is not None:
                    elapsed = (current_dt - last_seen_dt).total_seconds()
                    if 0 <= elapsed < ERROR_DEDUPE_WINDOW_SECONDS:
                        return

            conn.execute(
                """
                INSERT INTO error_events (ts, component, error_type, message, context_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (timestamp, component_name, error_type, message, context_json),
            )
            conn.execute(
                """
                INSERT INTO monitor_state(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (state_key, timestamp, _utc_now()),
            )
            conn.commit()
    except Exception:
        logger.warning("Failed to persist error telemetry for component=%s", component, exc_info=True)


def get_recent_errors(limit: int = 10) -> list[dict[str, str]]:
    safe_limit = max(1, min(int(limit), 30))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT ts, component, error_type, message
            FROM error_events
            ORDER BY ts DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [
        {
            "ts": str(row[0]),
            "component": str(row[1]),
            "error_type": str(row[2]),
            "message": str(row[3]),
        }
        for row in rows
    ]


def get_error_summary(since_iso: str, until_iso: str, limit: int = 10) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 50))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT component, COUNT(*) AS c
            FROM error_events
            WHERE ts >= ? AND ts < ?
            GROUP BY component
            ORDER BY c DESC, component ASC
            LIMIT ?
            """,
            (since_iso, until_iso, safe_limit),
        ).fetchall()
    return [{"component": str(row[0]), "count": int(row[1])} for row in rows]


def get_error_count(since_iso: str, until_iso: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM error_events WHERE ts >= ? AND ts < ?",
            (since_iso, until_iso),
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def get_event_counts(event: str | None, since_iso: str, until_iso: str) -> dict[str, int]:
    with _connect() as conn:
        if event:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM analytics_events
                WHERE event = ? AND ts >= ? AND ts < ?
                """,
                (event, since_iso, until_iso),
            ).fetchone()
            return {event: int(row[0]) if row else 0}

        rows = conn.execute(
            """
            SELECT event, COUNT(*)
            FROM analytics_events
            WHERE ts >= ? AND ts < ?
            GROUP BY event
            """,
            (since_iso, until_iso),
        ).fetchall()
    result: dict[str, int] = {}
    for row in rows:
        result[str(row[0])] = int(row[1])
    return result


def get_funnel(since_iso: str, until_iso: str) -> dict[str, int]:
    counts = get_event_counts(None, since_iso, until_iso)
    return {
        "leads_ingested": int(counts.get("lead_ingested", 0)),
        "leads_matched": int(counts.get("lead_matched", 0)),
        "leads_sent": int(counts.get("lead_sent", 0)),
        "upgrade_requested": int(counts.get("upgrade_requested", 0)),
        "checkout_created": int(counts.get("checkout_created", 0)),
        "payment_confirmed": int(counts.get("payment_confirmed", 0)),
        "pro_activated": int(counts.get("pro_activated", 0)),
    }


def get_lead_quality_metrics(since_iso: str, until_iso: str) -> dict[str, Any]:
    with _connect() as conn:
        dist_rows = conn.execute(
            """
            SELECT match_level, COUNT(*)
            FROM analytics_events
            WHERE event = 'lead_matched'
              AND ts >= ?
              AND ts < ?
              AND match_level IS NOT NULL
            GROUP BY match_level
            """,
            (since_iso, until_iso),
        ).fetchall()
        avg_row = conn.execute(
            """
            SELECT AVG(score)
            FROM analytics_events
            WHERE event = 'lead_matched'
              AND ts >= ?
              AND ts < ?
              AND score IS NOT NULL
            """,
            (since_iso, until_iso),
        ).fetchone()
        filtered_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM analytics_events
            WHERE event = 'lead_filtered'
              AND ts >= ?
              AND ts < ?
            """,
            (since_iso, until_iso),
        ).fetchone()
        ingested_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM analytics_events
            WHERE event = 'lead_ingested'
              AND ts >= ?
              AND ts < ?
            """,
            (since_iso, until_iso),
        ).fetchone()

    distribution: dict[str, int] = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for row in dist_rows:
        level = str(row[0]).upper()
        distribution[level] = int(row[1])
    filtered = int(filtered_row[0]) if filtered_row else 0
    ingested = int(ingested_row[0]) if ingested_row else 0
    filtered_pct = (100.0 * filtered / ingested) if ingested > 0 else 0.0
    return {
        "match_level_distribution": distribution,
        "avg_score": float(avg_row[0]) if avg_row and avg_row[0] is not None else None,
        "filtered_count": filtered,
        "ingested_count": ingested,
        "filtered_pct": filtered_pct,
    }


def _json_extract_supported() -> bool:
    global _JSON_EXTRACT_SUPPORTED
    if _JSON_EXTRACT_SUPPORTED is not None:
        return _JSON_EXTRACT_SUPPORTED
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT json_extract('{\"a\":1}', '$.a')"
            ).fetchone()
        _JSON_EXTRACT_SUPPORTED = bool(row and str(row[0]) == "1")
    except Exception:
        _JSON_EXTRACT_SUPPORTED = False
    return _JSON_EXTRACT_SUPPORTED


def get_score_buckets(since_iso: str, until_iso: str) -> dict[str, int]:
    buckets = {
        "0-24": 0,
        "25-49": 0,
        "50-69": 0,
        "70-84": 0,
        "85-100": 0,
        "101+": 0,
    }
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT score, COUNT(*)
            FROM analytics_events
            WHERE event = 'lead_matched'
              AND ts >= ?
              AND ts < ?
              AND score IS NOT NULL
            GROUP BY score
            """,
            (since_iso, until_iso),
        ).fetchall()

    for row in rows:
        score = int(row[0])
        count = int(row[1])
        if score <= 24:
            buckets["0-24"] += count
        elif score <= 49:
            buckets["25-49"] += count
        elif score <= 69:
            buckets["50-69"] += count
        elif score <= 84:
            buckets["70-84"] += count
        elif score <= 100:
            buckets["85-100"] += count
        else:
            buckets["101+"] += count
    return buckets


def get_level_distribution(since_iso: str, until_iso: str) -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT match_level, COUNT(*)
            FROM analytics_events
            WHERE event = 'lead_matched'
              AND ts >= ?
              AND ts < ?
              AND match_level IS NOT NULL
            GROUP BY match_level
            """,
            (since_iso, until_iso),
        ).fetchall()
    distribution = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for row in rows:
        distribution[str(row[0]).upper()] = int(row[1])
    return distribution


def get_filtered_metrics(since_iso: str, until_iso: str) -> dict[str, Any]:
    with _connect() as conn:
        ingested_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM analytics_events
            WHERE event = 'lead_ingested'
              AND ts >= ?
              AND ts < ?
            """,
            (since_iso, until_iso),
        ).fetchone()
        filtered_row = conn.execute(
            """
            SELECT COUNT(*)
            FROM analytics_events
            WHERE event = 'lead_filtered'
              AND ts >= ?
              AND ts < ?
            """,
            (since_iso, until_iso),
        ).fetchone()
    ingested = int(ingested_row[0]) if ingested_row else 0
    filtered = int(filtered_row[0]) if filtered_row else 0
    filtered_rate = (float(filtered) / float(ingested)) if ingested > 0 else 0.0
    return {
        "ingested": ingested,
        "filtered": filtered,
        "filtered_rate": filtered_rate,
    }


def get_block_reasons(since_iso: str, until_iso: str) -> dict[str, Any]:
    reasons: dict[str, int] = {}
    by_plan: dict[str, dict[str, int]] = {}
    if _json_extract_supported():
        try:
            with _connect() as conn:
                reason_rows = conn.execute(
                    """
                    SELECT COALESCE(json_extract(meta_json, '$.reason'), 'unknown') AS reason, COUNT(*)
                    FROM analytics_events
                    WHERE event = 'lead_blocked'
                      AND ts >= ?
                      AND ts < ?
                    GROUP BY reason
                    """,
                    (since_iso, until_iso),
                ).fetchall()
                plan_rows = conn.execute(
                    """
                    SELECT COALESCE(plan, 'UNKNOWN') AS p,
                           COALESCE(json_extract(meta_json, '$.reason'), 'unknown') AS reason,
                           COUNT(*)
                    FROM analytics_events
                    WHERE event = 'lead_blocked'
                      AND ts >= ?
                      AND ts < ?
                    GROUP BY p, reason
                    """,
                    (since_iso, until_iso),
                ).fetchall()
            for row in reason_rows:
                reasons[str(row[0])] = int(row[1])
            for row in plan_rows:
                plan = str(row[0]).upper()
                reason = str(row[1])
                by_plan.setdefault(plan, {})
                by_plan[plan][reason] = int(row[2])
            return {"reasons": reasons, "by_plan": by_plan}
        except Exception:
            logger.warning("Analytics JSON mode failed, falling back", exc_info=True)

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT plan, meta_json
            FROM analytics_events
            WHERE event = 'lead_blocked'
              AND ts >= ?
              AND ts < ?
            """,
            (since_iso, until_iso),
        ).fetchall()
    for row in rows:
        plan = str(row[0]).upper() if row[0] else "UNKNOWN"
        reason = "unknown"
        meta_json = row[1]
        if meta_json:
            try:
                parsed = json.loads(str(meta_json))
                if isinstance(parsed, dict):
                    reason = str(parsed.get("reason") or "unknown")
            except Exception:
                reason = "unknown"
        reasons[reason] = reasons.get(reason, 0) + 1
        by_plan.setdefault(plan, {})
        by_plan[plan][reason] = by_plan[plan].get(reason, 0) + 1
    return {"reasons": reasons, "by_plan": by_plan}


def get_source_stats(since_iso: str, until_iso: str, limit: int = 15) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 50))
    source_counts: dict[str, dict[str, int]] = {}

    if _json_extract_supported():
        try:
            with _connect() as conn:
                rows = conn.execute(
                    """
                    SELECT COALESCE(json_extract(meta_json, '$.source'), 'unknown') AS source,
                           event,
                           COUNT(*)
                    FROM analytics_events
                    WHERE event IN ('lead_ingested', 'lead_matched', 'lead_sent')
                      AND ts >= ?
                      AND ts < ?
                    GROUP BY source, event
                    """,
                    (since_iso, until_iso),
                ).fetchall()
            for row in rows:
                source = str(row[0])
                event = str(row[1])
                count = int(row[2])
                source_counts.setdefault(source, {"ingested": 0, "matched": 0, "sent": 0})
                if event == "lead_ingested":
                    source_counts[source]["ingested"] += count
                elif event == "lead_matched":
                    source_counts[source]["matched"] += count
                elif event == "lead_sent":
                    source_counts[source]["sent"] += count
        except Exception:
            logger.warning("Analytics JSON mode failed, falling back", exc_info=True)
            source_counts = {}

    if not source_counts:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT event, meta_json
                FROM analytics_events
                WHERE event IN ('lead_ingested', 'lead_matched', 'lead_sent')
                  AND ts >= ?
                  AND ts < ?
                """,
                (since_iso, until_iso),
            ).fetchall()
        for row in rows:
            event = str(row[0])
            source = "unknown"
            meta_json = row[1]
            if meta_json:
                try:
                    parsed = json.loads(str(meta_json))
                    if isinstance(parsed, dict):
                        source = str(parsed.get("source") or "unknown")
                except Exception:
                    source = "unknown"
            source_counts.setdefault(source, {"ingested": 0, "matched": 0, "sent": 0})
            if event == "lead_ingested":
                source_counts[source]["ingested"] += 1
            elif event == "lead_matched":
                source_counts[source]["matched"] += 1
            elif event == "lead_sent":
                source_counts[source]["sent"] += 1

    ranked = sorted(
        (
            {
                "source": source,
                "ingested": counts["ingested"],
                "matched": counts["matched"],
                "sent": counts["sent"],
            }
            for source, counts in source_counts.items()
        ),
        key=lambda item: (item["ingested"], item["matched"], item["sent"]),
        reverse=True,
    )
    return ranked[:safe_limit]


def activate_pro_for_user(user_id: int, reason: str, activated_at_iso: str) -> None:
    mark_user_pro(user_id=user_id, enabled=True, activated_at_iso=activated_at_iso, plan="PRO")
    activated_at = _parse_iso_utc(activated_at_iso) or datetime.now(timezone.utc)
    dedupe_since = (activated_at - timedelta(hours=1)).isoformat()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM analytics_events
            WHERE user_id = ?
              AND event = 'pro_activated'
              AND ts >= ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (user_id, dedupe_since),
        ).fetchone()
    if row is not None:
        return
    log_event(
        "pro_activated",
        user_id=user_id,
        plan="PRO",
        meta={"reason": reason},
        ts=activated_at_iso,
    )


def downgrade_user_to_free(user_id: int, reason: str, updated_at_iso: str) -> None:
    mark_user_pro(user_id=user_id, enabled=False, activated_at_iso=updated_at_iso, plan="FREE")
    log_event(
        "pro_downgraded",
        user_id=user_id,
        plan="FREE",
        meta={"reason": reason},
        ts=updated_at_iso,
    )


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


def _legacy_min_level_to_skill_matches(value: str | None) -> int:
    normalized = str(value or "").upper().strip()
    return LEGACY_MIN_LEVEL_TO_SKILL_MATCHES.get(normalized, DEFAULT_MIN_SKILL_MATCHES)


def _normalize_min_skill_matches(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MIN_SKILL_MATCHES
    return max(1, min(parsed, 20))


def get_user_settings(user_id: int) -> tuple[int, int | None]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT min_level, min_skill_matches, daily_cap FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return DEFAULT_MIN_SKILL_MATCHES, None
    legacy_min_level = str(row[0]).upper().strip() if row[0] else ""
    min_skill_matches_raw = row[1]
    min_skill_matches = (
        _normalize_min_skill_matches(min_skill_matches_raw)
        if min_skill_matches_raw is not None
        else _legacy_min_level_to_skill_matches(legacy_min_level)
    )
    daily_cap = row[2]
    return min_skill_matches, int(daily_cap) if daily_cap is not None else None


def set_user_min_level(user_id: int, min_level: str) -> None:
    # Backward-compatible bridge from legacy level setting to min_skill_matches.
    min_skill_matches = _legacy_min_level_to_skill_matches(min_level)
    set_user_min_skill_matches(user_id, min_skill_matches)


def set_user_min_skill_matches(user_id: int, min_skill_matches: int) -> None:
    normalized = _normalize_min_skill_matches(min_skill_matches)
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, min_level, min_skill_matches, daily_cap, updated_at)
            VALUES (?, 'MEDIUM', ?, NULL, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                min_skill_matches=excluded.min_skill_matches,
                daily_cap=user_settings.daily_cap,
                updated_at=excluded.updated_at
            """,
            (user_id, normalized, now),
        )
        conn.commit()

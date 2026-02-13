"""Runtime health checks and lightweight alerting for bot operations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot

from app import db as db_module
from app.config import get_admin_user_ids, get_stripe_secret_key, get_stripe_webhook_secret
from app.webhooks import is_stripe_webhook_enabled

logger = logging.getLogger(__name__)

SEVERITY_INFO = "INFO"
SEVERITY_WARN = "WARN"
SEVERITY_CRITICAL = "CRITICAL"

INGESTION_WARN_MINUTES = 30
INGESTION_CRITICAL_MINUTES = 120
DELIVERY_WARN_MINUTES = 60
BLOCKED_RATIO_WARN = 0.80
CHECKOUT_WITHOUT_PAYMENT_WARN_HOURS = 6
WEBHOOK_STALE_CRITICAL_HOURS = 6
PAYMENT_FRESHNESS_INFO_DAYS = 7

COOLDOWN_MINUTES_BY_SEVERITY = {
    SEVERITY_INFO: 12 * 60,
    SEVERITY_WARN: 60,
    SEVERITY_CRITICAL: 15,
}

MONITOR_INTERVAL_SECONDS = 5 * 60


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(db_module.DB_PATH, uri=db_module.DB_URI)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _minutes_since(iso_ts: str | None) -> float | None:
    dt = _parse_iso(iso_ts)
    if dt is None:
        return None
    return (_utc_now() - dt).total_seconds() / 60.0


def _window_iso(hours: int = 0, days: int = 0) -> tuple[str, str]:
    now = _utc_now()
    since = now - timedelta(hours=hours, days=days)
    return since.isoformat(), now.isoformat()


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def get_last_event_ts(event: str) -> str | None:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT MAX(ts) FROM analytics_events WHERE event = ?",
                (event,),
            ).fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception:
        logger.warning("get_last_event_ts failed for event=%s", event, exc_info=True)
        return None


def count_events(event: str, since_iso: str, until_iso: str) -> int:
    try:
        with _connect() as conn:
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
    except Exception:
        logger.warning("count_events failed for event=%s", event, exc_info=True)
        return 0


def _get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM monitor_state WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _set_state(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        """
        INSERT INTO monitor_state(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at
        """,
        (key, value, utc_now_iso()),
    )


def _delete_state(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM monitor_state WHERE key = ?", (key,))


def set_silence(alert_type: str, minutes: int) -> str:
    safe_minutes = max(1, int(minutes))
    until = (_utc_now() + timedelta(minutes=safe_minutes)).isoformat()
    try:
        with _connect() as conn:
            _set_state(conn, f"silence:{alert_type}", until)
            conn.commit()
    except Exception:
        logger.warning("set_silence failed for alert_type=%s", alert_type, exc_info=True)
    return until


def clear_silence(alert_type: str) -> None:
    try:
        with _connect() as conn:
            _delete_state(conn, f"silence:{alert_type}")
            conn.commit()
    except Exception:
        logger.warning("clear_silence failed for alert_type=%s", alert_type, exc_info=True)


def get_silence_until(alert_type: str) -> str | None:
    try:
        with _connect() as conn:
            return _get_state(conn, f"silence:{alert_type}")
    except Exception:
        logger.warning("get_silence_until failed for alert_type=%s", alert_type, exc_info=True)
        return None


def is_silenced(alert_type: str) -> bool:
    until = get_silence_until(alert_type)
    until_dt = _parse_iso(until)
    if until_dt is None:
        return False
    return _utc_now() < until_dt


def should_send_alert(alert_type: str, cooldown_minutes: int) -> bool:
    if is_silenced(alert_type):
        return False

    try:
        with _connect() as conn:
            value = _get_state(conn, f"alert:last_sent:{alert_type}")
    except Exception:
        logger.warning("should_send_alert state read failed for alert_type=%s", alert_type, exc_info=True)
        return True

    last_dt = _parse_iso(value)
    if last_dt is None:
        return True

    elapsed_minutes = (_utc_now() - last_dt).total_seconds() / 60.0
    return elapsed_minutes >= max(0, int(cooldown_minutes))


def record_alert(alert: dict[str, Any]) -> None:
    ts = str(alert.get("ts") or utc_now_iso())
    alert_type = str(alert.get("type") or "unknown")
    severity = str(alert.get("severity") or SEVERITY_INFO)
    message = str(alert.get("message") or "")
    meta_json = None
    meta = alert.get("meta")
    if isinstance(meta, dict):
        meta_json = json.dumps(meta, ensure_ascii=True, separators=(",", ":"))

    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO monitor_alerts(ts, alert_type, severity, message, meta_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, alert_type, severity, message, meta_json),
            )
            _set_state(conn, f"alert:last_sent:{alert_type}", ts)
            conn.commit()
    except Exception:
        logger.warning("record_alert failed for alert_type=%s", alert_type, exc_info=True)


def get_recent_alerts(limit: int = 10) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 50))
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT ts, severity, alert_type, message
                FROM monitor_alerts
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
    except Exception:
        logger.warning("get_recent_alerts failed", exc_info=True)
        return []

    return [
        {
            "ts": str(row[0]),
            "severity": str(row[1]),
            "type": str(row[2]),
            "message": str(row[3]),
        }
        for row in rows
    ]


def _alert(alert_type: str, severity: str, message: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ts": utc_now_iso(),
        "type": alert_type,
        "severity": severity,
        "message": message,
        "meta": meta or {},
    }


def _worst(current: str, candidate: str) -> str:
    order = {"OK": 0, SEVERITY_INFO: 1, SEVERITY_WARN: 2, SEVERITY_CRITICAL: 3, "FAIL": 4}
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


def get_health_snapshot() -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []
    snapshot: dict[str, Any] = {
        "ingestion": {"status": "OK", "last_ts": None},
        "delivery": {"status": "OK", "last_ts": None, "blocked_ratio_24h": 0.0},
        "billing": {"status": "OK", "last_payment_ts": None, "webhook_last_ts": None},
        "db": {"status": "OK", "detail": "SELECT 1"},
    }

    try:
        with _connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        snapshot["db"]["status"] = "FAIL"
        snapshot["db"]["detail"] = str(exc)
        alerts.append(_alert("db_unhealthy", SEVERITY_CRITICAL, "DB health query failed (SELECT 1)."))
        return {"alerts": alerts, "components": snapshot}

    now = _utc_now()

    last_ingested = get_last_event_ts("lead_ingested")
    snapshot["ingestion"]["last_ts"] = last_ingested
    ingested_minutes = _minutes_since(last_ingested)
    if ingested_minutes is None or ingested_minutes > INGESTION_CRITICAL_MINUTES:
        snapshot["ingestion"]["status"] = _worst(str(snapshot["ingestion"]["status"]), SEVERITY_CRITICAL)
        alerts.append(
            _alert(
                "ingestion_stalled",
                SEVERITY_CRITICAL,
                f"No lead_ingested in >{INGESTION_CRITICAL_MINUTES}m (last: {last_ingested or 'never'}).",
                {"last_ts": last_ingested},
            )
        )
    elif ingested_minutes > INGESTION_WARN_MINUTES:
        snapshot["ingestion"]["status"] = _worst(str(snapshot["ingestion"]["status"]), SEVERITY_WARN)
        alerts.append(
            _alert(
                "ingestion_lagging",
                SEVERITY_WARN,
                f"No lead_ingested in {int(ingested_minutes)}m (warn>{INGESTION_WARN_MINUTES}m).",
                {"last_ts": last_ingested},
            )
        )

    last_sent = get_last_event_ts("lead_sent")
    snapshot["delivery"]["last_ts"] = last_sent
    sent_minutes = _minutes_since(last_sent)

    since_24h = (now - timedelta(hours=24)).isoformat()
    until_now = now.isoformat()
    ingested_24h = count_events("lead_ingested", since_24h, until_now)
    matched_24h = count_events("lead_matched", since_24h, until_now)
    sent_24h = count_events("lead_sent", since_24h, until_now)
    blocked_24h = count_events("lead_blocked", since_24h, until_now)
    blocked_den = blocked_24h + sent_24h
    blocked_ratio = (blocked_24h / blocked_den) if blocked_den > 0 else 0.0
    snapshot["delivery"]["blocked_ratio_24h"] = blocked_ratio

    if (ingested_24h > 0 or matched_24h > 0) and (sent_minutes is None or sent_minutes > DELIVERY_WARN_MINUTES):
        snapshot["delivery"]["status"] = _worst(str(snapshot["delivery"]["status"]), SEVERITY_WARN)
        alerts.append(
            _alert(
                "delivery_stalled",
                SEVERITY_WARN,
                f"No lead_sent in {int(sent_minutes) if sent_minutes is not None else 'N/A'}m while ingested_24h={ingested_24h}, matched_24h={matched_24h}.",
                {"last_ts": last_sent, "ingested_24h": ingested_24h, "matched_24h": matched_24h},
            )
        )

    if blocked_den > 0 and blocked_ratio > BLOCKED_RATIO_WARN:
        snapshot["delivery"]["status"] = _worst(str(snapshot["delivery"]["status"]), SEVERITY_WARN)
        alerts.append(
            _alert(
                "delivery_blocked_ratio_high",
                SEVERITY_WARN,
                f"lead_blocked ratio is {blocked_ratio * 100.0:.1f}% in last 24h.",
                {"blocked_24h": blocked_24h, "sent_24h": sent_24h},
            )
        )

    stripe_configured = bool(get_stripe_secret_key() and get_stripe_webhook_secret())
    webhook_enabled, _ = is_stripe_webhook_enabled()

    if stripe_configured:
        last_payment = get_last_event_ts("payment_confirmed")
        snapshot["billing"]["last_payment_ts"] = last_payment
        payment_minutes = _minutes_since(last_payment)
        if payment_minutes is None or payment_minutes > PAYMENT_FRESHNESS_INFO_DAYS * 24 * 60:
            snapshot["billing"]["status"] = _worst(str(snapshot["billing"]["status"]), SEVERITY_INFO)
            alerts.append(
                _alert(
                    "billing_no_recent_payment",
                    SEVERITY_INFO,
                    f"No payment_confirmed in last {PAYMENT_FRESHNESS_INFO_DAYS}d (last: {last_payment or 'never'}).",
                )
            )

        since_6h = (now - timedelta(hours=CHECKOUT_WITHOUT_PAYMENT_WARN_HOURS)).isoformat()
        checkout_6h = count_events("checkout_created", since_6h, until_now)
        payment_6h = count_events("payment_confirmed", since_6h, until_now)
        if checkout_6h > 0 and payment_6h == 0:
            snapshot["billing"]["status"] = _worst(str(snapshot["billing"]["status"]), SEVERITY_WARN)
            alerts.append(
                _alert(
                    "webhook_payment_gap",
                    SEVERITY_WARN,
                    f"checkout_created={checkout_6h} and payment_confirmed=0 in last {CHECKOUT_WITHOUT_PAYMENT_WARN_HOURS}h.",
                    {"checkout_6h": checkout_6h, "payment_6h": payment_6h},
                )
            )

        if webhook_enabled:
            processed_last: str | None = None
            try:
                with _connect() as conn:
                    if _has_table(conn, "processed_events"):
                        row = conn.execute(
                            "SELECT MAX(created_at) FROM processed_events WHERE provider = 'stripe'",
                        ).fetchone()
                        processed_last = str(row[0]) if row and row[0] else None
            except Exception:
                logger.warning("Failed reading processed_events freshness", exc_info=True)

            snapshot["billing"]["webhook_last_ts"] = processed_last
            stale_minutes = _minutes_since(processed_last)
            if (
                checkout_6h > 0
                and payment_6h == 0
                and (stale_minutes is None or stale_minutes > WEBHOOK_STALE_CRITICAL_HOURS * 60)
            ):
                snapshot["billing"]["status"] = _worst(str(snapshot["billing"]["status"]), SEVERITY_CRITICAL)
                alerts.append(
                    _alert(
                        "webhook_stalled",
                        SEVERITY_CRITICAL,
                        f"Webhook appears stale: last processed stripe event={processed_last or 'never'} with checkout_created traffic in last {CHECKOUT_WITHOUT_PAYMENT_WARN_HOURS}h.",
                        {"checkout_6h": checkout_6h, "last_processed": processed_last},
                    )
                )

    db_path = db_module.DB_PATH
    if not db_module.DB_URI and os.path.exists(db_path):
        try:
            size_bytes = os.path.getsize(db_path)
            if size_bytes > 512 * 1024 * 1024:
                snapshot["db"]["status"] = _worst(str(snapshot["db"]["status"]), SEVERITY_INFO)
                alerts.append(
                    _alert(
                        "db_size_large",
                        SEVERITY_INFO,
                        f"SQLite DB file is large ({size_bytes // (1024 * 1024)} MB).",
                        {"size_bytes": size_bytes},
                    )
                )
        except Exception:
            logger.warning("Failed to inspect DB size", exc_info=True)

    return {"alerts": alerts, "components": snapshot}


def run_health_checks() -> list[dict[str, Any]]:
    try:
        snapshot = get_health_snapshot()
        alerts = snapshot.get("alerts", [])
        return list(alerts) if isinstance(alerts, list) else []
    except Exception:
        logger.warning("run_health_checks failed", exc_info=True)
        return []


def format_alert_message(alert: dict[str, Any]) -> str:
    severity = str(alert.get("severity") or SEVERITY_INFO)
    alert_type = str(alert.get("type") or "unknown")
    message = str(alert.get("message") or "")
    return f"[{severity}] {alert_type}: {message}"


async def _send_to_admins(bot: Bot, text: str) -> None:
    admin_ids = get_admin_user_ids()
    if not admin_ids:
        return
    for admin_id in sorted(admin_ids):
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            logger.warning("Failed to send monitor alert to admin_id=%s", admin_id, exc_info=True)


async def run_monitor_loop(bot: Bot, interval_seconds: int = MONITOR_INTERVAL_SECONDS) -> None:
    safe_interval = max(30, int(interval_seconds))
    logger.info("Monitoring loop started (interval=%ss)", safe_interval)

    while True:
        try:
            alerts = run_health_checks()
            for alert in alerts:
                alert_type = str(alert.get("type") or "unknown")
                severity = str(alert.get("severity") or SEVERITY_INFO)
                cooldown = COOLDOWN_MINUTES_BY_SEVERITY.get(severity, 60)
                if not should_send_alert(alert_type, cooldown_minutes=cooldown):
                    continue
                await _send_to_admins(bot, format_alert_message(alert))
                record_alert(alert)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Monitoring loop iteration failed", exc_info=True)

        await asyncio.sleep(safe_interval)

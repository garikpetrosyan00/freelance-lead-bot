"""Admin monitoring and alerting commands."""

from __future__ import annotations

import os
import subprocess
from importlib.util import find_spec
from datetime import datetime, timedelta, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message

from app import db
from app.config import demo_mode_enabled, enable_telegram_ingestion
from app.monitoring import clear_silence, get_health_snapshot, get_recent_alerts, get_silence_until, set_silence
from app.ops.auth import require_admin, require_super_admin
from app.ops.validation import parse_int, validate_alert_type, validate_limit
from app.version import version_text

router = Router()


def _parse_limit(raw: str | None, default: int = 10) -> tuple[int | None, str | None]:
    token = (raw or "").strip()
    if not token:
        return default, None
    parts = token.split()
    if len(parts) != 1:
        return None, "Invalid limit. Use one integer between 1 and 50."
    parsed = parse_int(parts[0], min=1, max=50, default=None)
    if parsed is None:
        return None, "Invalid limit. Use one integer between 1 and 50."
    return validate_limit(parsed, 50), None


def _render_health_summary(snapshot: dict[str, object]) -> list[str]:
    components = snapshot.get("components", {})
    if not isinstance(components, dict):
        return ["Health check unavailable."]

    ingestion = components.get("ingestion", {}) if isinstance(components.get("ingestion"), dict) else {}
    delivery = components.get("delivery", {}) if isinstance(components.get("delivery"), dict) else {}
    billing = components.get("billing", {}) if isinstance(components.get("billing"), dict) else {}
    db = components.get("db", {}) if isinstance(components.get("db"), dict) else {}

    blocked_ratio = float(delivery.get("blocked_ratio_24h", 0.0)) * 100.0
    lines = [
        "Health summary:",
        f"ingestion: {ingestion.get('status', 'OK')} | last lead_ingested: {ingestion.get('last_ts') or '-'}",
        f"delivery: {delivery.get('status', 'OK')} | last lead_sent: {delivery.get('last_ts') or '-'} | blocked_ratio_24h: {blocked_ratio:.1f}%",
        f"billing/webhook: {billing.get('status', 'OK')} | last payment_confirmed: {billing.get('last_payment_ts') or '-'} | last processed webhook: {billing.get('webhook_last_ts') or '-'}",
        f"db: {db.get('status', 'OK')} | {db.get('detail') or '-'}",
    ]

    alerts = snapshot.get("alerts", [])
    if isinstance(alerts, list) and alerts:
        lines.append("active alerts:")
        for alert in alerts[:6]:
            if not isinstance(alert, dict):
                continue
            lines.append(
                f"- [{alert.get('severity', 'INFO')}] {alert.get('type', 'unknown')}: {alert.get('message', '')}"
            )
    else:
        lines.append("active alerts: none")

    return lines


def _parse_hours(raw: str | None, default: int = 24) -> tuple[int | None, str | None]:
    token = (raw or "").strip()
    if not token:
        return default, None
    parts = token.split()
    if len(parts) != 1:
        return None, "Invalid hours. Use one integer between 1 and 168."
    parsed = parse_int(parts[0], min=1, max=168, default=None)
    if parsed is None:
        return None, "Invalid hours. Use one integer between 1 and 168."
    return parsed, None


def _iso_window(hours: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    return since.isoformat(), now.isoformat()


def _top_block_reason(since_iso: str, until_iso: str) -> str:
    block_stats = db.get_block_reasons(since_iso, until_iso).get("reasons", {})
    if not isinstance(block_stats, dict) or not block_stats:
        return "-"
    reason, count = max(
        ((str(key), int(value)) for key, value in block_stats.items()),
        key=lambda item: item[1],
    )
    return f"{reason} ({count})"


def _db_journal_mode() -> str:
    with db._connect() as conn:
        row = conn.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]).lower() if row and row[0] else "unknown"


def _db_size_bytes() -> int:
    if db.DB_URI or db.DB_PATH == ":memory:":
        return 0
    try:
        return os.path.getsize(db.DB_PATH)
    except OSError:
        return 0


def _monitor_alert_stats(since_iso: str, until_iso: str) -> tuple[int, str]:
    with db._connect() as conn:
        count_row = conn.execute(
            "SELECT COUNT(*) FROM monitor_alerts WHERE ts >= ? AND ts < ?",
            (since_iso, until_iso),
        ).fetchone()
        last_row = conn.execute("SELECT MAX(ts) FROM monitor_alerts").fetchone()
    total = int(count_row[0]) if count_row and count_row[0] is not None else 0
    last_ts = str(last_row[0]) if last_row and last_row[0] else "-"
    return total, last_ts


def _format_diag(hours: int) -> list[str]:
    since_iso, until_iso = _iso_window(hours)
    counts = db.get_event_counts(None, since_iso, until_iso)
    alerts_count, last_alert_ts = _monitor_alert_stats(since_iso, until_iso)
    error_summary = db.get_error_summary(since_iso, until_iso, limit=3)
    total_errors = db.get_error_count(since_iso, until_iso)
    recent_errors = db.get_recent_errors(limit=3)

    top_components = (
        ", ".join(f"{item['component']}:{item['count']}" for item in error_summary[:3])
        if error_summary
        else "-"
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    backup_dir = os.getenv("BACKUP_DIR", "backups")
    blocked = int(counts.get("lead_blocked", 0))

    lines = [
        f"DIAG ({hours}h UTC)",
        f"now_utc: {now_iso}",
        f"db_path: {db.DB_PATH}",
        f"journal_mode: {_db_journal_mode()}",
        f"db_size_bytes: {_db_size_bytes()}",
        f"backup_dir: {backup_dir}",
        (
            "activity: "
            f"lead_ingested={int(counts.get('lead_ingested', 0))} "
            f"lead_sent={int(counts.get('lead_sent', 0))} "
            f"lead_blocked={blocked} top_block_reason={_top_block_reason(since_iso, until_iso)} "
            f"checkout_created={int(counts.get('checkout_created', 0))} "
            f"payment_confirmed={int(counts.get('payment_confirmed', 0))} "
            f"pro_activated={int(counts.get('pro_activated', 0))}"
        ),
        f"monitoring: alerts_last_{hours}h={alerts_count} last_alert_ts={last_alert_ts}",
        f"errors: total_last_{hours}h={total_errors} top_components={top_components}",
    ]
    if recent_errors:
        lines.append("recent_errors:")
        for row in recent_errors:
            lines.append(
                f"- {row['ts']} | {row['component']} | {row['error_type']} | {row['message'][:120]}"
            )
    else:
        lines.append("recent_errors: none")
    return lines


def _present(value: str | None) -> str:
    return "yes" if bool((value or "").strip()) else "no"


def _db_exists() -> str:
    if db.DB_URI or db.DB_PATH == ":memory:":
        return "n/a"
    return "yes" if os.path.exists(db.DB_PATH) else "no"


def _module_installed(module_name: str) -> str:
    return "yes" if find_spec(module_name) is not None else "no"


def _git_commit_short() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        ).strip()
    except Exception:
        return None
    return out or None


@router.message(Command("health"))
async def handle_health(message: Message) -> None:
    if not await require_admin(message):
        return

    snapshot = get_health_snapshot()
    await message.answer("\n".join(_render_health_summary(snapshot)))


@router.message(Command("diag"))
async def handle_diag(message: Message, command: CommandObject) -> None:
    if not await require_admin(message):
        return
    hours, err = _parse_hours(command.args, default=24)
    if err:
        await message.answer("Usage: /diag [hours]\nExample: /diag 48")
        return
    await message.answer("\n".join(_format_diag(hours or 24)))


@router.message(Command("errors_recent"))
async def handle_errors_recent(message: Message, command: CommandObject) -> None:
    if not await require_admin(message):
        return
    limit, err = _parse_limit(command.args, default=10)
    if err:
        await message.answer("Usage: /errors_recent [limit]\nExample: /errors_recent 20")
        return
    safe_limit = max(1, min(int(limit or 10), 30))
    rows = db.get_recent_errors(limit=safe_limit)
    if not rows:
        await message.answer("No error telemetry found.")
        return

    lines = [f"Recent errors ({safe_limit}):"]
    for row in rows:
        lines.append(f"{row['ts']} | {row['component']} | {row['error_type']} | {row['message'][:120]}")
    await message.answer("\n".join(lines))


@router.message(Command("alerts_recent"))
async def handle_alerts_recent(message: Message, command: CommandObject) -> None:
    if not await require_admin(message):
        return

    limit, err = _parse_limit(command.args, default=10)
    if err:
        await message.answer("Usage: /alerts_recent [limit]\nExample: /alerts_recent 20")
        return

    rows = get_recent_alerts(limit=limit or 10)
    if not rows:
        await message.answer("No monitor alerts found.")
        return

    lines = ["Recent monitor alerts:"]
    for row in rows:
        lines.append(f"{row['ts']} | {row['severity']} | {row['type']} | {row['message']}")
    await message.answer("\n".join(lines))


@router.message(Command("silence"))
async def handle_silence(message: Message, command: CommandObject) -> None:
    if not await require_super_admin(message):
        return

    args = (command.args or "").strip().split()
    if len(args) != 2:
        await message.answer("Usage: /silence <alert_type> <minutes>\nExample: /silence ingestion_stalled 120")
        return

    alert_type = validate_alert_type(args[0])
    minutes = parse_int(args[1], min=1, max=1440, default=None)
    if not alert_type or minutes is None:
        await message.answer("Usage: /silence <alert_type> <minutes>\nExample: /silence ingestion_stalled 120")
        return

    until = set_silence(alert_type, int(minutes))
    await message.answer(f"Silenced '{alert_type}' until {until}")


@router.message(Command("unsilence"))
async def handle_unsilence(message: Message, command: CommandObject) -> None:
    if not await require_super_admin(message):
        return

    args = (command.args or "").strip().split()
    if len(args) != 1:
        await message.answer("Usage: /unsilence <alert_type>\nExample: /unsilence ingestion_stalled")
        return

    alert_type = validate_alert_type(args[0])
    if not alert_type:
        await message.answer("Usage: /unsilence <alert_type>\nExample: /unsilence ingestion_stalled")
        return
    previous = get_silence_until(alert_type)
    clear_silence(alert_type)
    if previous:
        await message.answer(f"Unsilenced '{alert_type}' (previous until: {previous}).")
        return
    await message.answer(f"Unsilenced '{alert_type}'.")


@router.message(Command("doctor"))
async def handle_doctor(message: Message) -> None:
    if not await require_admin(message):
        return

    demo_mode = demo_mode_enabled()
    telegram_ingestion = enable_telegram_ingestion() and not demo_mode
    tg_api_id_present = _present(os.getenv("TG_API_ID"))
    tg_api_hash_present = _present(os.getenv("TG_API_HASH"))
    stripe_secret_canonical_present = _present(os.getenv("STRIPE_SECRET"))
    stripe_secret_legacy_present = _present(os.getenv("STRIPE_SECRET_KEY"))
    stripe_secret_present = "yes" if (
        stripe_secret_canonical_present == "yes" or stripe_secret_legacy_present == "yes"
    ) else "no"
    stripe_webhook_secret_present = _present(os.getenv("STRIPE_WEBHOOK_SECRET"))
    stripe_enabled = (not demo_mode) and stripe_secret_present == "yes" and stripe_webhook_secret_present == "yes"
    now_local = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    commit = _git_commit_short()

    lines = [
        "🩺 Doctor",
        f"DEMO_MODE: {'ON' if demo_mode else 'OFF'}",
        f"Telegram ingestion: {'enabled' if telegram_ingestion else 'disabled'}",
        f"httpx installed: {_module_installed('httpx')}",
        f"feedparser installed: {_module_installed('feedparser')}",
        f"TG_API_ID present: {tg_api_id_present}",
        f"TG_API_HASH present: {tg_api_hash_present}",
        f"Stripe: {'enabled' if stripe_enabled else 'disabled'}",
        f"STRIPE_SECRET present: {stripe_secret_present}",
        f"STRIPE_SECRET_KEY legacy set: {stripe_secret_legacy_present}",
        f"STRIPE_WEBHOOK_SECRET present: {stripe_webhook_secret_present}",
        f"DB path: {db.DB_PATH}",
        f"DB exists: {_db_exists()}",
        f"Local time: {now_local}",
    ]
    if commit:
        lines.append(f"Commit: {commit}")
    lines.append(f"Version: {version_text()}")

    await message.answer("\n".join(lines))

"""Admin monitoring and alerting commands."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message

from app.config import is_admin
from app.monitoring import clear_silence, get_health_snapshot, get_recent_alerts, get_silence_until, set_silence

router = Router()


def _is_admin(message: Message) -> bool:
    user = message.from_user
    return bool(user and is_admin(user.id))


def _parse_limit(raw: str | None, default: int = 10) -> tuple[int | None, str | None]:
    if not raw or not raw.strip():
        return default, None
    parts = raw.strip().split()
    if len(parts) != 1 or not parts[0].isdigit():
        return None, "Invalid limit. Use one integer between 1 and 50."
    limit = int(parts[0])
    if limit < 1 or limit > 50:
        return None, "Invalid limit. Use one integer between 1 and 50."
    return limit, None


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


@router.message(Command("health"))
async def handle_health(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    snapshot = get_health_snapshot()
    await message.answer("\n".join(_render_health_summary(snapshot)))


@router.message(Command("alerts_recent"))
async def handle_alerts_recent(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
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
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    args = (command.args or "").strip().split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Usage: /silence <alert_type> <minutes>\nExample: /silence ingestion_stalled 120")
        return

    alert_type = args[0].strip()
    minutes = int(args[1])
    if not alert_type or minutes < 1:
        await message.answer("Usage: /silence <alert_type> <minutes>\nExample: /silence ingestion_stalled 120")
        return

    until = set_silence(alert_type, minutes)
    await message.answer(f"Silenced '{alert_type}' until {until}")


@router.message(Command("unsilence"))
async def handle_unsilence(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    args = (command.args or "").strip().split()
    if len(args) != 1 or not args[0].strip():
        await message.answer("Usage: /unsilence <alert_type>\nExample: /unsilence ingestion_stalled")
        return

    alert_type = args[0].strip()
    previous = get_silence_until(alert_type)
    clear_silence(alert_type)
    if previous:
        await message.answer(f"Unsilenced '{alert_type}' (previous until: {previous}).")
        return
    await message.answer(f"Unsilenced '{alert_type}'.")

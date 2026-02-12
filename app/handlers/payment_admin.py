"""Admin diagnostics for Stripe payments and subscriptions."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message

from app.billing import get_user_subscription_status, mask_id, reconcile_user_payment
from app.config import is_admin
from app.db import get_latest_payment_for_user, get_user_plan, list_recent_payments, list_subscriptions_by_status

router = Router()


def _is_admin(message: Message) -> bool:
    user = message.from_user
    return bool(user and is_admin(user.id))


def _parse_limit(raw: str | None, default: int = 20) -> tuple[int | None, str | None]:
    if not raw or not raw.strip():
        return default, None
    parts = raw.strip().split()
    if len(parts) != 1:
        return None, "Invalid limit. Use one integer between 1 and 200."
    token = parts[0]
    if not token.isdigit():
        return None, "Invalid limit. Use one integer between 1 and 200."
    limit = int(token)
    if limit < 1 or limit > 200:
        return None, "Invalid limit. Use one integer between 1 and 200."
    return limit, None


@router.message(Command("payments_recent"))
async def handle_payments_recent(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    limit, err = _parse_limit(command.args, default=20)
    if err:
        await message.answer("Usage: /payments_recent [limit]\nExample: /payments_recent 30")
        return

    rows = list_recent_payments(limit=limit or 20)
    if not rows:
        await message.answer("No payments found.")
        return

    lines = ["Recent payments:"]
    for row in rows:
        lines.append(
            f"#{row['id']} user={row['user_id']} status={row['status']} "
            f"amount={row['amount_total'] if row['amount_total'] is not None else '-'} "
            f"currency={row['currency'] or '-'} created={row['created_at']} "
            f"session={mask_id(str(row['checkout_session_id'] or ''))} "
            f"sub={mask_id(str(row['subscription_id'] or ''))}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("subs_past_due"))
async def handle_subs_past_due(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    limit, err = _parse_limit(command.args, default=20)
    if err:
        await message.answer("Usage: /subs_past_due [limit]\nExample: /subs_past_due 50")
        return

    rows = list_subscriptions_by_status("past_due", limit=limit or 20)
    if not rows:
        await message.answer("No past_due subscriptions found.")
        return

    lines = ["past_due subscriptions:"]
    for row in rows:
        lines.append(
            f"user={row['user_id']} status={row['status']} "
            f"sub={mask_id(str(row['subscription_id'] or ''))} "
            f"updated={row['updated_at']}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("force_sync_user"))
async def handle_force_sync_user(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    args = (command.args or "").strip()
    if not args or len(args.split()) != 1 or not args.isdigit() or int(args) <= 0:
        await message.answer("Usage: /force_sync_user <user_id>\nExample: /force_sync_user 123456789")
        return

    user_id = int(args)
    result = reconcile_user_payment(user_id=user_id, force=True)
    latest = get_latest_payment_for_user(user_id)
    plan = get_user_plan(user_id)
    sub_status, sub_id = get_user_subscription_status(user_id)

    lines = [
        f"force_sync_user result for {user_id}:",
        f"attempted={result.attempted}",
        f"changed={result.changed}",
        f"message={result.message}",
        f"plan={plan}",
        f"payment_status={latest['status'] if latest else '-'}",
        f"session={mask_id(str(latest['checkout_session_id'] if latest else ''))}",
        f"sub_status={sub_status or '-'}",
        f"sub_id={mask_id(sub_id)}",
    ]
    await message.answer("\n".join(lines))

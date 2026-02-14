"""Plan-related commands."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import get_payment_link, get_upgrade_contact
from app.db import get_daily_usage, get_plan, set_plan, utc_day
from app.gating import FREE_DAILY_CAP, allowed_match_levels
from app.ops.auth import require_admin
from app.ops.validation import parse_choice, parse_int

router = Router()


@router.message(Command("plan"))
async def handle_plan(message: Message) -> None:
    user_id = message.from_user.id
    plan = get_plan(user_id)
    allowed = ", ".join(sorted(allowed_match_levels(plan)))
    day = utc_day()
    used = get_daily_usage(user_id, day)
    lines = [
        f"Plan: {plan}",
        f"Allowed: {allowed}",
    ]
    if plan == "FREE":
        lines.append(f"Daily cap (UTC): {FREE_DAILY_CAP}")
    lines.append(f"Used today: {used}")
    lines.append("Tip: /buy_pro for Stripe checkout, or /upgrade for manual flow")
    await message.answer("\n".join(lines))


@router.message(Command("upgrade"))
async def handle_upgrade(message: Message) -> None:
    contact = get_upgrade_contact()
    payment_link = get_payment_link()
    lines = [
        "PRO benefits:",
        "- LOW alerts",
        "- Custom min level",
        "- Custom daily cap",
        "- Faster cooldown",
        "- Unlimited cap (if configured)",
    ]
    if payment_link:
        lines.append(f"Pay here: {payment_link}")
    else:
        lines.append("Payment link coming soon.")
    lines.append(f"After payment, message {contact} with your /my_id.")
    lines.append("Daily cap is based on UTC day.")
    await message.answer("\n".join(lines))


@router.message(Command("set_plan"))
async def handle_set_plan(message: Message) -> None:
    if not await require_admin(message):
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Usage: /set_plan FREE|PRO")
        return

    plan = parse_choice(parts[1], {"FREE", "PRO"})
    if plan is None:
        await message.answer("Plan must be FREE or PRO.")
        return
    target_id = message.from_user.id
    if len(parts) >= 3:
        parsed_target = parse_int(parts[2], min=1, max=2_147_483_647, default=None)
        if parsed_target is None:
            await message.answer("Invalid user ID.")
            return
        target_id = int(parsed_target)
    try:
        set_plan(target_id, str(plan))
    except ValueError:
        await message.answer("Plan must be FREE or PRO.")
        return

    await message.answer(f"Plan for user {target_id} set to {plan}.")

"""Plan-related commands."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import get_admin_user_ids
from app.db import get_daily_usage, get_plan, set_plan, utc_day
from app.gating import FREE_DAILY_CAP, allowed_match_levels

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
    lines.append("Tip: /upgrade to get LOW alerts + unlimited")
    await message.answer("\n".join(lines))


@router.message(Command("upgrade"))
async def handle_upgrade(message: Message) -> None:
    await message.answer(
        "To upgrade to PRO, contact @your_username or use payment link (coming soon)."
    )


@router.message(Command("set_plan"))
async def handle_set_plan(message: Message) -> None:
    admin_ids = get_admin_user_ids()
    if message.from_user.id not in admin_ids:
        await message.answer("Not authorized.")
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Usage: /set_plan FREE|PRO")
        return

    plan = parts[1].upper().strip()
    target_id = message.from_user.id
    if len(parts) >= 3:
        if not parts[2].isdigit() or int(parts[2]) <= 0:
            await message.answer("Invalid user ID.")
            return
        target_id = int(parts[2])
    try:
        set_plan(target_id, plan)
    except ValueError:
        await message.answer("Plan must be FREE or PRO.")
        return

    await message.answer(f"Plan for user {target_id} set to {plan}.")

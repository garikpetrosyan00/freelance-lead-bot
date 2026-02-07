"""PRO settings commands."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import (
    get_daily_usage,
    get_plan,
    get_user_settings,
    set_user_daily_cap,
    set_user_min_level,
    utc_day,
)
from app.gating import FREE_DAILY_CAP, effective_cap

router = Router()


def _upgrade_tip() -> str:
    return "Upgrade to PRO to customize match level and daily cap."


@router.message(Command("settings"))
async def handle_settings(message: Message) -> None:
    user_id = message.from_user.id
    plan = get_plan(user_id)
    min_level, user_cap = get_user_settings(user_id)
    day = utc_day()
    used_today = get_daily_usage(user_id, day)
    cap = effective_cap(plan, user_cap)

    cap_text = "unlimited" if cap is None else str(cap)
    if plan == "FREE":
        cap_text = str(FREE_DAILY_CAP)

    lines = [
        f"Plan: {plan}",
        f"Min level: {min_level}",
        f"Daily cap (UTC): {cap_text}",
        f"Used today: {used_today}",
    ]
    if plan == "FREE":
        lines.append(_upgrade_tip())

    await message.answer("\n".join(lines))


@router.message(Command("set_min_level"))
async def handle_set_min_level(message: Message) -> None:
    plan = get_plan(message.from_user.id)
    if plan != "PRO":
        await message.answer(_upgrade_tip())
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Usage: /set_min_level LOW|MEDIUM|HIGH")
        return

    level = parts[1].upper().strip()
    try:
        set_user_min_level(message.from_user.id, level)
    except ValueError:
        await message.answer("Min level must be LOW, MEDIUM, or HIGH.")
        return

    await message.answer(f"Min level set to {level}.")


@router.message(Command("set_daily_cap"))
async def handle_set_daily_cap(message: Message) -> None:
    plan = get_plan(message.from_user.id)
    if plan != "PRO":
        await message.answer(_upgrade_tip())
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Usage: /set_daily_cap <N|unlimited>")
        return

    value = parts[1].lower().strip()
    if value == "unlimited":
        set_user_daily_cap(message.from_user.id, None)
        await message.answer("Daily cap set to unlimited.")
        return

    if not value.isdigit() or int(value) < 1:
        await message.answer("Daily cap must be a positive integer or 'unlimited'.")
        return

    set_user_daily_cap(message.from_user.id, int(value))
    await message.answer(f"Daily cap set to {value}.")

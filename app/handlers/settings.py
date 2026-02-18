"""PRO settings commands."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import (
    get_daily_usage,
    get_plan,
    get_skills,
    get_user_settings,
    min_skill_matches_validation_error,
    set_user_min_skill_matches,
    utc_day,
)
from app.gating import FREE_DAILY_CAP, effective_cap

router = Router()


def _upgrade_tip() -> str:
    return "Upgrade to PRO for unlimited daily leads."


@router.message(Command("settings"))
async def handle_settings(message: Message) -> None:
    user_id = message.from_user.id
    plan = get_plan(user_id)
    min_skill_matches, _ = get_user_settings(user_id)
    day = utc_day()
    used_today = get_daily_usage(user_id, day)
    daily_limit = effective_cap(plan, None)

    limit_text = "Unlimited" if daily_limit is None else str(daily_limit)
    if plan == "FREE":
        limit_text = str(FREE_DAILY_CAP)

    lines = [
        f"Plan: {plan}",
        f"Minimum skill matches: {int(min_skill_matches)}",
        f"Daily lead limit: {limit_text}",
        f"Used today: {used_today}",
    ]
    if len(get_skills(user_id)) < 3:
        lines.append("Add more skills in Skills to improve matching.")

    await message.answer("\n".join(lines))


@router.message(Command("set_min_matches", "set_min_level"))
async def handle_set_min_level(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Usage: /set_min_matches <N>")
        return

    if not parts[1].isdigit():
        await message.answer("Minimum skill matches must be a number from 1 to 20.")
        return
    value = int(parts[1])
    if value < 1 or value > 20:
        await message.answer("Minimum skill matches must be a number from 1 to 20.")
        return
    skills_count = len(get_skills(message.from_user.id))
    validation_error = min_skill_matches_validation_error(value, skills_count)
    if validation_error is not None:
        await message.answer(validation_error)
        return

    set_user_min_skill_matches(message.from_user.id, value)

    await message.answer(f"Minimum skill matches set to {value}.")

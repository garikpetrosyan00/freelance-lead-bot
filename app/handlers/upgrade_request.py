"""/request_pro command handler."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import get_plan, get_user_settings
from app.gating import FREE_DAILY_CAP, effective_cap

router = Router()


@router.message(Command("request_pro"))
async def handle_request_pro(message: Message) -> None:
    user = message.from_user
    user_id = user.id
    username = f"@{user.username}" if user.username else "(none)"
    plan = get_plan(user_id)
    min_level, user_cap = get_user_settings(user_id)
    cap = effective_cap(plan, user_cap)

    cap_text = "unlimited" if cap is None else str(cap)
    if plan == "FREE":
        cap_text = str(FREE_DAILY_CAP)

    lines = [
        "PRO request:",
        f"user_id: {user_id}",
        f"username: {username}",
        f"plan: {plan}",
        f"settings: min_level={min_level}, daily_cap={cap_text}",
        "paid: yes/no (user to answer)",
    ]
    await message.answer("\n".join(lines))

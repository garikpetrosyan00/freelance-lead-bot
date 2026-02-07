"""Subscription commands."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import get_skills, is_subscribed, set_subscription
from app.pipeline import run_once

router = Router()


@router.message(Command("subscribe"))
async def handle_subscribe(message: Message) -> None:
    set_subscription(message.from_user.id, True)
    await message.answer("Subscribed ✅ You'll receive matching leads.")


@router.message(Command("unsubscribe"))
async def handle_unsubscribe(message: Message) -> None:
    set_subscription(message.from_user.id, False)
    await message.answer("Unsubscribed 📴 You will no longer receive leads.")


@router.message(Command("status"))
async def handle_status(message: Message) -> None:
    subscribed = is_subscribed(message.from_user.id)
    skills = get_skills(message.from_user.id)
    yes_no = "yes" if subscribed else "no"
    await message.answer(
        f"Subscribed: {yes_no}\nSaved skills: {len(skills)}"
    )


@router.message(Command("ping_lead"))
async def handle_ping_lead(message: Message) -> None:
    await run_once(message.bot)
    await message.answer("Triggered lead check ✅")

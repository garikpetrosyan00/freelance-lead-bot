"""Skill preference handlers."""

from __future__ import annotations

import shlex
from typing import List

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import get_skills, set_skills

router = Router()


def _parse_skills(text: str) -> List[str]:
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()

    if not parts:
        return []

    # Remove the command itself (/set_skills)
    return parts[1:]


@router.message(Command("set_skills"))
async def handle_set_skills(message: Message) -> None:
    text = message.text or ""
    skills = _parse_skills(text)
    if not skills:
        await message.answer(
            "Usage: /set_skills <skills...>\nExample: /set_skills python django react"
        )
        return

    set_skills(message.from_user.id, skills)
    saved = get_skills(message.from_user.id)
    await message.answer(f"Saved skills: {', '.join(saved)} ✅")


@router.message(Command("my_skills"))
async def handle_my_skills(message: Message) -> None:
    skills = get_skills(message.from_user.id)
    if not skills:
        await message.answer("No skills set yet. Use /set_skills ...")
        return

    await message.answer(f"Your skills: {', '.join(skills)}")

"""Skill preference handlers."""

from __future__ import annotations

import shlex
from typing import List

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app import db
from app.ui.skills_picker import init_picker_state, skills_picker_kb, skills_picker_text

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
        state = init_picker_state(message.from_user.id, db.get_skills(message.from_user.id))
        sent = await message.answer(
            skills_picker_text(len(state["selected"])),
            reply_markup=skills_picker_kb(message.from_user.id),
        )
        state["message_id"] = sent.message_id
        return

    db.set_skills(message.from_user.id, skills)
    saved = db.get_skills(message.from_user.id)
    await message.answer(f"Saved skills: {', '.join(saved)} ✅")


@router.message(Command("my_skills"))
async def handle_my_skills(message: Message) -> None:
    skills = db.get_skills(message.from_user.id)
    if not skills:
        await message.answer("No skills set yet. Use /set_skills ...")
        return

    await message.answer(f"Your skills: {', '.join(skills)}")


@router.message(Command("debug_skills"))
async def handle_debug_skills(message: Message) -> None:
    user_id = message.from_user.id
    skills = db.get_skills(user_id)
    snapshot = db.get_skills_storage_snapshot(user_id)
    lines = [
        f"user_id: {user_id}",
        f"get_skills: {skills}",
        f"user_prefs.row_exists: {snapshot['row_exists']}",
        f"user_prefs.raw_skills: {snapshot['raw_skills']}",
        f"user_prefs.updated_at: {snapshot['updated_at']}",
    ]
    await message.answer("\n".join(lines))

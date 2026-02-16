"""Interactive skill picker with autocomplete suggestions."""

from __future__ import annotations

import hashlib

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import get_skills, set_skills
from app.skills_catalog import normalize, search_skills

router = Router()

ACTIVE_SKILL_PICKERS: dict[int, str] = {}
SKILL_ID_MAP: dict[int, dict[str, str]] = {}


def _skill_id(skill: str) -> str:
    return hashlib.sha1(normalize(skill).encode("utf-8")).hexdigest()[:10]


def _sorted_unique_skills(skills: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for skill in skills:
        value = skill.strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered


def _picker_text(query: str, selected: list[str]) -> str:
    selected_text = ", ".join(selected) if selected else "None"
    if query.strip():
        return (
            f"Skill picker query: {query.strip()}\n"
            "Tap to toggle skills.\n"
            f"Selected: {selected_text}"
        )
    return (
        "Skill picker mode is active.\n"
        "Type a skill (e.g. 'py', 'react'). I will suggest options.\n"
        f"Selected: {selected_text}"
    )


def _picker_keyboard(user_id: int, query: str) -> InlineKeyboardMarkup:
    selected = set(get_skills(user_id))
    suggestions = search_skills(query, limit=12)
    id_map: dict[str, str] = {}
    rows: list[list[InlineKeyboardButton]] = []
    for skill in suggestions:
        skill_id = _skill_id(skill)
        id_map[skill_id] = skill
        prefix = "✅" if skill in selected else "➕"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix} {skill}",
                    callback_data=f"skill:toggle_id:{skill_id}",
                )
            ]
        )
    SKILL_ID_MAP[user_id] = id_map
    rows.append(
        [
            InlineKeyboardButton(text="Done", callback_data="skill:done"),
            InlineKeyboardButton(text="Clear", callback_data="skill:clear"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_picker(message: Message, user_id: int, query: str) -> None:
    selected = _sorted_unique_skills(get_skills(user_id))
    await message.answer(
        _picker_text(query, selected),
        reply_markup=_picker_keyboard(user_id, query),
    )


async def _refresh_picker(callback: CallbackQuery, user_id: int, query: str) -> None:
    selected = _sorted_unique_skills(get_skills(user_id))
    if callback.message:
        await callback.message.edit_text(
            _picker_text(query, selected),
            reply_markup=_picker_keyboard(user_id, query),
        )


@router.message(Command("skills"))
async def handle_skills_picker(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    ACTIVE_SKILL_PICKERS[user.id] = ""
    await message.answer("Type a skill (e.g. 'py', 'react'). I will suggest options.")
    await _send_picker(message, user.id, "")


@router.message(F.text)
async def handle_skills_query(message: Message) -> None:
    user = message.from_user
    if user is None or user.id not in ACTIVE_SKILL_PICKERS:
        return
    text = (message.text or "").strip()
    if text.startswith("/"):
        return
    ACTIVE_SKILL_PICKERS[user.id] = text
    await _send_picker(message, user.id, text)


@router.callback_query(F.data.startswith("skill:toggle_id:"))
async def handle_skill_toggle_by_id(callback: CallbackQuery) -> None:
    user = callback.from_user
    skill_id = (callback.data or "").split("skill:toggle_id:", 1)[1].strip()
    skill = SKILL_ID_MAP.get(user.id, {}).get(skill_id, "").strip()
    if not skill:
        await callback.answer("Expired, type again")
        return

    existing = _sorted_unique_skills(get_skills(user.id))
    existing_keys = {value.lower(): value for value in existing}
    skill_key = skill.lower()

    if skill_key in existing_keys:
        updated = [value for value in existing if value.lower() != skill_key]
    else:
        updated = existing + [skill]

    set_skills(user.id, updated)
    query = ACTIVE_SKILL_PICKERS.get(user.id, "")
    ACTIVE_SKILL_PICKERS[user.id] = query
    await _refresh_picker(callback, user.id, query)
    await callback.answer("Updated")


@router.callback_query(F.data == "skill:clear")
async def handle_skill_clear(callback: CallbackQuery) -> None:
    user = callback.from_user
    set_skills(user.id, [])
    query = ACTIVE_SKILL_PICKERS.get(user.id, "")
    ACTIVE_SKILL_PICKERS[user.id] = query
    await _refresh_picker(callback, user.id, query)
    await callback.answer("Cleared")


@router.callback_query(F.data == "skill:done")
async def handle_skill_done(callback: CallbackQuery) -> None:
    user = callback.from_user
    ACTIVE_SKILL_PICKERS.pop(user.id, None)
    selected = _sorted_unique_skills(get_skills(user.id))
    summary = ", ".join(selected) if selected else "No skills selected."
    if callback.message:
        await callback.message.answer(f"Selected skills: {summary}")
    await callback.answer("Done")

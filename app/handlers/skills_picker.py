"""Interactive skill picker with autocomplete suggestions."""

from __future__ import annotations

import hashlib

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import get_skills, set_skills
from app.skills_catalog import normalize, search_skills

router = Router()

ACTIVE_SKILL_PICKERS: dict[int, str] = {}
SKILL_ID_MAP: dict[int, dict[str, str]] = {}
PICKER_MESSAGE_ID: dict[int, int] = {}


def _skill_id(skill: str) -> str:
    return hashlib.sha1(normalize(skill).encode("utf-8")).hexdigest()[:10]


def _clean_custom_skill(s: str) -> str:
    cleaned = " ".join((s or "").strip().split())
    if not cleaned:
        return ""
    return cleaned[:50]


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
    count = len(selected)
    if count == 0:
        selected_text = "None"
    else:
        preview = ", ".join(selected[:5])
        selected_text = f"{preview}, ..." if count > 5 else preview
    selected_line = f"Selected ({count}): {selected_text}"
    if query.strip():
        return f"Skill picker query: {query.strip()}\nTap to toggle skills.\n{selected_line}"
    return (
        "Skill picker mode is active.\n"
        "Type a skill (e.g. 'py', 'react'). I will suggest options.\n"
        f"{selected_line}"
    )


def _picker_keyboard(user_id: int, query: str) -> InlineKeyboardMarkup:
    selected_skills = _sorted_unique_skills(get_skills(user_id))
    selected = set(selected_skills)
    selected_lower = {skill.lower() for skill in selected_skills}
    suggestions = search_skills(query, limit=12)
    suggestions_lower = {skill.lower() for skill in suggestions}
    id_map: dict[str, str] = {}
    rows: list[list[InlineKeyboardButton]] = []

    custom = _clean_custom_skill(query)
    if custom:
        custom_lower = custom.lower()
        if custom_lower not in selected_lower and custom_lower not in suggestions_lower:
            custom_id = _skill_id(custom)
            id_map[custom_id] = custom
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f'➕ Add "{custom}"',
                        callback_data=f"skill:add_custom:{custom_id}",
                    )
                ]
            )

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
    SKILL_ID_MAP.setdefault(user_id, {}).update(id_map)
    rows.append(
        [
            InlineKeyboardButton(
                text="➖ Remove last",
                callback_data="skill:remove_last",
            )
        ]
    )
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
        PICKER_MESSAGE_ID[user_id] = callback.message.message_id


async def _show_or_update_picker(bot: Bot, user_id: int, chat_id: int, query: str) -> None:
    selected = _sorted_unique_skills(get_skills(user_id))
    text = _picker_text(query, selected)
    markup = _picker_keyboard(user_id, query)

    message_id = PICKER_MESSAGE_ID.get(user_id)
    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=markup,
            )
            return
        except Exception:
            pass

    msg = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
    )
    PICKER_MESSAGE_ID[user_id] = msg.message_id


@router.message(Command("skills"))
async def handle_skills_picker(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None or message.chat is None:
        return
    ACTIVE_SKILL_PICKERS[user.id] = ""
    await _show_or_update_picker(bot, user.id, message.chat.id, "")


@router.message(F.text & ~F.text.startswith("/"))
async def handle_skills_query(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None or message.chat is None or user.id not in ACTIVE_SKILL_PICKERS:
        return
    text = (message.text or "").strip()
    if text.startswith("/"):
        return
    ACTIVE_SKILL_PICKERS[user.id] = text
    await _show_or_update_picker(bot, user.id, message.chat.id, text)


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


@router.callback_query(F.data.startswith("skill:add_custom:"))
async def handle_skill_add_custom(callback: CallbackQuery) -> None:
    user = callback.from_user
    skill_id = (callback.data or "").split("skill:add_custom:", 1)[1].strip()
    skill = _clean_custom_skill(SKILL_ID_MAP.get(user.id, {}).get(skill_id, ""))
    if not skill:
        await callback.answer("Expired, type again")
        return

    existing = _sorted_unique_skills(get_skills(user.id))
    existing_keys = {value.lower() for value in existing}
    if skill.lower() not in existing_keys:
        set_skills(user.id, existing + [skill])

    query = ACTIVE_SKILL_PICKERS.get(user.id, "")
    await _refresh_picker(callback, user.id, query)
    await callback.answer("Added")


@router.callback_query(F.data == "skill:clear")
async def handle_skill_clear(callback: CallbackQuery) -> None:
    user = callback.from_user
    set_skills(user.id, [])
    query = ACTIVE_SKILL_PICKERS.get(user.id, "")
    ACTIVE_SKILL_PICKERS[user.id] = query
    await _refresh_picker(callback, user.id, query)
    await callback.answer("Cleared")


@router.callback_query(F.data == "skill:remove_last")
async def handle_skill_remove_last(callback: CallbackQuery) -> None:
    user = callback.from_user
    existing = _sorted_unique_skills(get_skills(user.id))
    if existing:
        updated = existing[:-1]
        set_skills(user.id, updated)
    query = ACTIVE_SKILL_PICKERS.get(user.id, "")
    await _refresh_picker(callback, user.id, query)
    await callback.answer("Removed last")


@router.callback_query(F.data == "skill:done")
async def handle_skill_done(callback: CallbackQuery) -> None:
    user = callback.from_user
    ACTIVE_SKILL_PICKERS.pop(user.id, None)
    PICKER_MESSAGE_ID.pop(user.id, None)
    SKILL_ID_MAP.pop(user.id, None)
    selected = _sorted_unique_skills(get_skills(user.id))
    count = len(selected)
    if count == 0:
        summary = "None"
    else:
        preview = ", ".join(selected[:5])
        summary = f"{preview}, ..." if count > 5 else preview
    final_text = f"✅ Skill picker closed.\nSelected ({count}): {summary}"
    if callback.message:
        await callback.message.edit_text(final_text)
    await callback.answer("Done")

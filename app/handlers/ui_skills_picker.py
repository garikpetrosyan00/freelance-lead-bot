"""Button-based skills picker handlers."""

from __future__ import annotations

from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram import Bot
from aiogram.types import CallbackQuery, Message

from app import db
from app.ops.usage import get_usage_summary
from app.ui.screens import home_kb, home_text
from app.ui.state import UI_MESSAGE_ID, render_ui_message
from app.ui.skills_picker import (
    ACTIVE_SKILL_PICKERS,
    PickerState,
    init_picker_state,
    normalize_skill,
    skills_picker_kb,
    skills_picker_text,
)

router = Router()


def _state_or_init(user_id: int, *, force_new: bool = False) -> PickerState:
    if force_new:
        saved = db.get_skills(user_id)
        return init_picker_state(user_id, saved)
    state = ACTIVE_SKILL_PICKERS.get(user_id)
    if state is not None:
        return state
    saved = db.get_skills(user_id)
    return init_picker_state(user_id, saved)


def _selected_skills_from_state(state: PickerState) -> list[str]:
    selected_keys = state["selected"]
    selected_skills: list[str] = []
    for skill in state["all"]:
        if skill.lower() in selected_keys:
            selected_skills.append(normalize_skill(skill))
    return selected_skills


def _persist_picker_selection(user_id: int, state: PickerState | None) -> list[str]:
    if state is None:
        # If picker state is missing, keep already persisted skills unchanged.
        return db.get_skills(user_id)
    selected_skills = _selected_skills_from_state(state)
    db.set_skills(user_id, selected_skills)
    return db.get_skills(user_id)


def _set_message_id(user_id: int, message_id: int | None) -> None:
    state = ACTIVE_SKILL_PICKERS.get(user_id)
    if state is None:
        return
    state["message_id"] = message_id
    if message_id is not None:
        UI_MESSAGE_ID[user_id] = message_id


def _bind_callback_message(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message):
        return
    _set_message_id(callback.from_user.id, callback.message.message_id)


async def _edit_or_send_message(
    *,
    bot: Bot,
    user_id: int,
    chat_id: int,
    text: str,
    reply_markup=None,
) -> None:
    state = ACTIVE_SKILL_PICKERS.get(user_id)
    target_message_id = state.get("message_id") if state else None
    if target_message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=target_message_id,
                text=text,
                reply_markup=reply_markup,
            )
            UI_MESSAGE_ID[user_id] = target_message_id
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                with suppress(TelegramBadRequest):
                    await bot.edit_message_reply_markup(
                        chat_id=chat_id,
                        message_id=target_message_id,
                        reply_markup=reply_markup,
                    )
                UI_MESSAGE_ID[user_id] = target_message_id
                return
        except TelegramAPIError:
            pass
    sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    UI_MESSAGE_ID[user_id] = sent.message_id
    _set_message_id(user_id, sent.message_id)


async def _show_picker(*, bot, user_id: int, chat_id: int) -> None:
    state = _state_or_init(user_id)
    state["awaiting_custom"] = False
    text = skills_picker_text(len(state["selected"]))
    await _edit_or_send_message(
        bot=bot,
        user_id=user_id,
        chat_id=chat_id,
        text=text,
        reply_markup=skills_picker_kb(user_id),
    )


async def _show_home(*, callback: CallbackQuery, user_id: int) -> None:
    usage = await get_usage_summary(db, user_id)
    plan = str(usage.get("plan") or db.get_plan(user_id))
    text = home_text(callback.from_user, plan, usage)
    chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
    await render_ui_message(
        callback.bot,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        reply_markup=home_kb(plan),
    )


@router.message(Command("skills"))
async def handle_skills_picker_command(message: Message) -> None:
    user = message.from_user
    if user is None or message.chat is None:
        return
    state = _state_or_init(user.id, force_new=True)
    sent = await message.answer(skills_picker_text(len(state["selected"])), reply_markup=skills_picker_kb(user.id))
    _set_message_id(user.id, sent.message_id)


@router.callback_query(F.data == "ui:skills")
async def handle_ui_skills(callback: CallbackQuery) -> None:
    try:
        user_id = callback.from_user.id
        state = _state_or_init(user_id, force_new=True)
        _bind_callback_message(callback)
        chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
        state["awaiting_custom"] = False
        rendered = await render_ui_message(
            callback.bot,
            chat_id=chat_id,
            user_id=user_id,
            text=skills_picker_text(len(state["selected"])),
            reply_markup=skills_picker_kb(user_id),
        )
        if rendered is not None:
            _set_message_id(user_id, rendered.message_id)
        elif user_id in UI_MESSAGE_ID:
            _set_message_id(user_id, UI_MESSAGE_ID[user_id])
    finally:
        await callback.answer()


@router.callback_query(F.data.startswith("skill:toggle:"))
async def handle_skill_toggle(callback: CallbackQuery) -> None:
    try:
        user_id = callback.from_user.id
        _bind_callback_message(callback)
        state = _state_or_init(user_id)
        idx_raw = (callback.data or "").split("skill:toggle:", 1)[1].strip()
        if not idx_raw.isdigit():
            chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
            await _show_picker(bot=callback.bot, user_id=user_id, chat_id=chat_id)
            return
        idx = int(idx_raw)
        all_skills = state["all"]
        if idx < 0 or idx >= len(all_skills):
            chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
            await _show_picker(bot=callback.bot, user_id=user_id, chat_id=chat_id)
            return

        key = all_skills[idx].lower()
        selected = state["selected"]
        if key in selected:
            selected.remove(key)
        else:
            selected.add(key)
        chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
        await _show_picker(bot=callback.bot, user_id=user_id, chat_id=chat_id)
    finally:
        await callback.answer()


@router.callback_query(F.data == "skill:prev")
async def handle_skill_prev(callback: CallbackQuery) -> None:
    try:
        user_id = callback.from_user.id
        _bind_callback_message(callback)
        state = _state_or_init(user_id)
        state["page"] = max(0, int(state.get("page", 0)) - 1)
        chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
        await _show_picker(bot=callback.bot, user_id=user_id, chat_id=chat_id)
    finally:
        await callback.answer()


@router.callback_query(F.data == "skill:next")
async def handle_skill_next(callback: CallbackQuery) -> None:
    try:
        user_id = callback.from_user.id
        _bind_callback_message(callback)
        state = _state_or_init(user_id)
        state["page"] = int(state.get("page", 0)) + 1
        chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
        await _show_picker(bot=callback.bot, user_id=user_id, chat_id=chat_id)
    finally:
        await callback.answer()


@router.callback_query(F.data == "skill:add")
async def handle_skill_add(callback: CallbackQuery) -> None:
    try:
        user_id = callback.from_user.id
        _bind_callback_message(callback)
        state = _state_or_init(user_id)
        state["awaiting_custom"] = True
        chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
        await _edit_or_send_message(
            bot=callback.bot,
            user_id=user_id,
            chat_id=chat_id,
            text="Send the skill name as a message (max 50 chars).",
            reply_markup=skills_picker_kb(user_id),
        )
    finally:
        await callback.answer()


@router.callback_query(F.data == "skill:done")
async def handle_skill_done(callback: CallbackQuery) -> None:
    try:
        user_id = callback.from_user.id
        _bind_callback_message(callback)
        state = ACTIVE_SKILL_PICKERS.get(user_id)
        selected_skills = _persist_picker_selection(user_id, state)
        ACTIVE_SKILL_PICKERS.pop(user_id, None)

        count = len(selected_skills)
        preview = ", ".join(selected_skills[:6]) if count else ""
        suffix = "..." if count > 6 else ""
        summary_text = f"✅ Saved {count} skills"
        if preview:
            summary_text += f": {preview}"
            if suffix:
                summary_text += f", {suffix}"

        chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
        await _edit_or_send_message(
            bot=callback.bot,
            user_id=user_id,
            chat_id=chat_id,
            text=summary_text,
        )
        await _show_home(callback=callback, user_id=user_id)
    finally:
        await callback.answer()


@router.callback_query(F.data == "skill:cancel")
async def handle_skill_cancel(callback: CallbackQuery) -> None:
    try:
        user_id = callback.from_user.id
        _bind_callback_message(callback)
        ACTIVE_SKILL_PICKERS.pop(user_id, None)
        await _show_home(callback=callback, user_id=user_id)
    finally:
        await callback.answer()


@router.message(F.text & ~F.text.startswith("/"))
async def handle_custom_skill_input(message: Message) -> None:
    user = message.from_user
    if user is None or message.chat is None:
        return
    if (message.text or "").startswith("/"):
        return

    state = ACTIVE_SKILL_PICKERS.get(user.id)
    if state is None or not state.get("awaiting_custom"):
        return

    raw = " ".join((message.text or "").strip().split())
    if not raw:
        await message.answer("Skill cannot be empty. Please try again.")
        return
    if len(raw) > 50:
        await message.answer("Skill is too long. Max 50 characters.")
        return

    cleaned = normalize_skill(raw)
    key = cleaned.lower()
    if key not in {skill.lower() for skill in state["all"]}:
        state["all"].append(cleaned)
    state["selected"].add(key)
    state["awaiting_custom"] = False

    await _edit_or_send_message(
        bot=message.bot,
        user_id=user.id,
        chat_id=message.chat.id,
        text=skills_picker_text(len(state["selected"])),
        reply_markup=skills_picker_kb(user.id),
    )

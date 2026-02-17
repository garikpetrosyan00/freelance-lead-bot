"""Inline settings handlers for gating preferences."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app import db
from app.gating import FREE_DAILY_CAP, effective_cap
from app.ui.settings_screen import settings_kb, settings_text
from app.ui.state import UI_MESSAGE_ID, render_ui_message
from app.ui.skills_picker import ACTIVE_SKILL_PICKERS

router = Router()


def _seed_ui_anchor(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        UI_MESSAGE_ID[callback.from_user.id] = callback.message.message_id


def _clear_picker_awaiting_custom(user_id: int) -> None:
    state = ACTIVE_SKILL_PICKERS.get(user_id)
    if state is None:
        return
    if state.get("awaiting_custom"):
        state["awaiting_custom"] = False


def _effective_display_settings(user_id: int) -> tuple[str, str, int | None]:
    plan = db.get_plan(user_id)
    min_level, _ = db.get_user_settings(user_id)
    if plan == "FREE":
        return plan, "MEDIUM", FREE_DAILY_CAP
    return plan, min_level, effective_cap(plan, None)


async def _render_settings(
    callback: CallbackQuery,
    *,
    notice: str | None = None,
    saved: bool = False,
    locked_hint: bool = False,
) -> None:
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if isinstance(callback.message, Message) else user_id
    if isinstance(callback.message, Message):
        UI_MESSAGE_ID[user_id] = callback.message.message_id
    plan, min_level, daily_limit = _effective_display_settings(user_id)
    text = settings_text(
        callback.from_user,
        plan,
        min_level,
        daily_limit,
        saved=saved,
        locked_hint=locked_hint,
    )
    if notice:
        text = f"{text}\n\n{notice}"
    markup = settings_kb(plan, min_level, daily_limit)
    await render_ui_message(
        callback.bot,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        reply_markup=markup,
    )


@router.callback_query(F.data == "ui:settings")
async def handle_ui_settings(callback: CallbackQuery) -> None:
    try:
        _seed_ui_anchor(callback)
        _clear_picker_awaiting_custom(callback.from_user.id)
        await _render_settings(callback)
    finally:
        await callback.answer()


@router.callback_query(F.data.startswith("set:min:"))
async def handle_set_min(callback: CallbackQuery) -> None:
    try:
        _seed_ui_anchor(callback)
        user_id = callback.from_user.id
        plan = db.get_plan(user_id)
        level = (callback.data or "").split("set:min:", 1)[1].strip().upper()
        if level not in {"LOW", "MEDIUM", "HIGH"}:
            await _render_settings(callback, notice="Invalid level.")
            return
        if plan != "PRO":
            await _render_settings(callback, locked_hint=True)
            return

        db.set_user_min_level(user_id, level)
        await _render_settings(callback, saved=True)
    finally:
        plan = db.get_plan(callback.from_user.id)
        if plan != "PRO":
            await callback.answer("PRO only")
        else:
            await callback.answer()


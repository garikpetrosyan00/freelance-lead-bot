"""Inline settings handlers for gating preferences."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app import db
from app.gating import effective_cap
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


def _effective_display_settings(user_id: int) -> tuple[str, int, int | None, int]:
    plan = db.get_plan(user_id)
    skills_count = len(db.get_skills(user_id))
    min_allowed, max_allowed = db.min_skill_matches_bounds_for_skills_count(skills_count)
    min_skill_matches, _ = db.get_user_settings(user_id)
    effective_min = min(max(int(min_skill_matches), min_allowed), max_allowed)
    return plan, effective_min, effective_cap(plan, None), skills_count


async def _render_settings(
    callback: CallbackQuery,
    *,
    notice: str | None = None,
    saved: bool = False,
) -> None:
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if isinstance(callback.message, Message) else user_id
    if isinstance(callback.message, Message):
        UI_MESSAGE_ID[user_id] = callback.message.message_id
    plan, min_skill_matches, daily_limit, skills_count = _effective_display_settings(user_id)
    text = settings_text(
        callback.from_user,
        plan,
        min_skill_matches,
        daily_limit,
        skills_count,
        saved=saved,
    )
    if notice:
        text = f"{text}\n\n{notice}"
    markup = settings_kb(plan, min_skill_matches, daily_limit, skills_count)
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


@router.callback_query(F.data == "ui:match_filter")
async def handle_ui_match_filter(callback: CallbackQuery) -> None:
    try:
        _seed_ui_anchor(callback)
        _clear_picker_awaiting_custom(callback.from_user.id)
        await _render_settings(callback)
    finally:
        await callback.answer()


@router.callback_query(F.data.startswith("set:min_skill_matches:"))
async def handle_set_min(callback: CallbackQuery) -> None:
    try:
        _seed_ui_anchor(callback)
        user_id = callback.from_user.id
        raw_value = (callback.data or "").split("set:min_skill_matches:", 1)[1].strip()
        if not raw_value.isdigit():
            await _render_settings(callback, notice="Invalid value.")
            return
        min_skill_matches = int(raw_value)
        skills_count = len(db.get_skills(user_id))
        validation_error = db.min_skill_matches_validation_error(min_skill_matches, skills_count)
        if validation_error is not None:
            await _render_settings(
                callback,
                notice=validation_error,
            )
            return

        db.set_user_min_skill_matches(user_id, min_skill_matches)
        await _render_settings(callback, saved=True)
    finally:
        await callback.answer()

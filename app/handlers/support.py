"""In-bot support flow and ticket forwarding."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import db
from app.analytics import log_event
from app.config import get_admin_chat_id, get_support_email
from app.gating import effective_cap
from app.ui.state import UI_MESSAGE_ID, render_ui_message

router = Router()
logger = logging.getLogger(__name__)


class SupportStates(StatesGroup):
    awaiting_support_message = State()


def _seed_ui_anchor(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        UI_MESSAGE_ID[callback.from_user.id] = callback.message.message_id


def _support_text() -> str:
    return (
        "Describe your issue and include screenshots if needed.\n"
        f"We'll reply by email. Support email: {get_support_email()}"
    )


def _support_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Write message", callback_data="ui:support:compose")],
            [InlineKeyboardButton(text="⬅ Back", callback_data="ui:home")],
        ]
    )


def _support_compose_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅ Back", callback_data="ui:support:cancel")],
        ]
    )


def support_delivery_fallback_text() -> str:
    return f"We couldn’t deliver your message in-chat right now. Please email us at {get_support_email()}."


def support_admin_chat_configured(admin_chat_id: int | None) -> bool:
    return isinstance(admin_chat_id, int) and admin_chat_id != 0


def _extract_support_message(message: Message) -> str | None:
    text = (message.text or "").strip()
    if text:
        return text
    caption = (message.caption or "").strip()
    if caption:
        return caption
    return None


def _settings_summary(user_id: int) -> str:
    plan = db.get_plan(user_id)
    min_skill_matches, _ = db.get_user_settings(user_id)
    skills_count = len(db.get_skills(user_id))
    daily_limit = effective_cap(plan, None)
    daily_limit_text = "Unlimited" if daily_limit is None else str(daily_limit)
    return (
        f"plan={plan}, min_skill_matches={int(min_skill_matches)}, "
        f"skills_count={skills_count}, daily_limit={daily_limit_text}"
    )


@router.callback_query(F.data == "ui:support")
async def handle_ui_support(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        _seed_ui_anchor(callback)
        await state.clear()
        chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
        await render_ui_message(
            callback.bot,
            chat_id=chat_id,
            user_id=callback.from_user.id,
            text=_support_text(),
            reply_markup=_support_kb(),
        )
    finally:
        await callback.answer()


@router.callback_query(F.data == "ui:support:compose")
async def handle_ui_support_compose(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        _seed_ui_anchor(callback)
        await state.set_state(SupportStates.awaiting_support_message)
        chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
        await render_ui_message(
            callback.bot,
            chat_id=chat_id,
            user_id=callback.from_user.id,
            text="Please describe your issue in one message.",
            reply_markup=_support_compose_kb(),
        )
    finally:
        await callback.answer()


@router.callback_query(F.data == "ui:support:cancel")
async def handle_ui_support_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    _seed_ui_anchor(callback)
    await state.clear()
    chat_id = callback.message.chat.id if isinstance(callback.message, Message) else callback.from_user.id
    await render_ui_message(
        callback.bot,
        chat_id=chat_id,
        user_id=callback.from_user.id,
        text=_support_text(),
        reply_markup=_support_kb(),
    )
    await callback.answer("Back")


@router.message(SupportStates.awaiting_support_message)
async def handle_support_ticket_message(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return

    support_text = _extract_support_message(message)
    if support_text is None:
        await message.answer("Please send your issue as text in one message.")
        return

    if db.has_recent_event(user_id=user.id, event="support_ticket_created", within_minutes=5):
        await message.answer(
            "You've recently contacted support. Please wait a few minutes before sending another message."
        )
        await state.clear()
        return

    username = str(user.username or "").strip()
    username_label = f"@{username}" if username else "(no username)"
    admin_chat_id = get_admin_chat_id()
    if not support_admin_chat_configured(admin_chat_id):
        logger.warning(
            "Support misconfigured: invalid ADMIN_CHAT_ID; user_id=%s username=%s admin_chat_id=%s",
            user.id,
            username_label,
            admin_chat_id,
        )
        await message.answer(support_delivery_fallback_text())
        await state.clear()
        return

    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    details = _settings_summary(user.id)
    admin_text = (
        "📩 Support ticket\n"
        f"User: {username_label}\n"
        f"Telegram ID: {user.id}\n"
        f"Time (UTC): {now_iso}\n"
        f"Settings: {details}\n\n"
        "Message:\n"
        f"{support_text}"
    )
    try:
        await message.bot.send_message(chat_id=admin_chat_id, text=admin_text)
    except Exception as exc:
        logger.warning(
            "Support forward failed; user_id=%s username=%s admin_chat_id=%s exception=%s message=%s",
            user.id,
            username_label,
            admin_chat_id,
            exc.__class__.__name__,
            str(exc),
            exc_info=True,
        )
        db.record_error(
            "admin",
            exc,
            context={"action": "support_ticket_forward", "admin_chat_id": admin_chat_id, "user_id": user.id},
        )
        await message.answer(support_delivery_fallback_text())
        await state.clear()
        return

    log_event(
        "support_ticket_created",
        user_id=user.id,
        plan=db.get_plan(user.id),
        meta={"source": "ui:support"},
    )
    await message.answer(f"✅ Sent to support. We'll respond via email: {get_support_email()}")
    await state.clear()

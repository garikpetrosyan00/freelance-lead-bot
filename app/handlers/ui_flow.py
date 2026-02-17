"""Inline UI flow callbacks for guided menu navigation."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import db
from app.analytics import log_event
from app.billing.stripe_checkout import create_checkout_session
from app.handlers.plan import render_upgrade_text
from app.handlers.test_lead import handle_test_lead
from app.ops.usage import get_usage_summary
from app.ui.screens import back_home_kb, home_kb, home_text
from app.ui.state import UI_MESSAGE_ID, render_ui_message
from app.ui.skills_picker import ACTIVE_SKILL_PICKERS

router = Router()


async def _edit_or_send(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
) -> None:
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if isinstance(callback.message, Message) else user_id
    if isinstance(callback.message, Message):
        UI_MESSAGE_ID[user_id] = callback.message.message_id
    await render_ui_message(
        callback.bot,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        reply_markup=reply_markup,
    )


def _value_or_dash(value: int | None) -> str:
    if isinstance(value, int):
        return str(value)
    return "—"


def _daily_limit_text(daily_limit: int | None, plan: str | None) -> str:
    if isinstance(daily_limit, int):
        return str(daily_limit)
    if plan == "PRO":
        return "Unlimited"
    return "—"


def _help_text() -> str:
    return (
        "Help\n"
        "- Set your skills: /skills or /set_skills <skills...>\n"
        "- Check your plan: /plan\n"
        "- Manage settings: /settings\n"
        "- Test matching: /test_lead\n"
        "- Subscribe/unsubscribe: /subscribe, /unsubscribe"
    )


def _upgrade_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back", callback_data="ui:home")],
        ]
    )


def _seed_ui_anchor(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        UI_MESSAGE_ID[callback.from_user.id] = callback.message.message_id


def _clear_picker_awaiting_custom(user_id: int) -> None:
    state = ACTIVE_SKILL_PICKERS.get(user_id)
    if state is None:
        return
    if state.get("awaiting_custom"):
        state["awaiting_custom"] = False


def _upgrade_checkout_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Open Checkout", url=url)],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="ui:home")],
        ]
    )


@router.callback_query(F.data == "ui:home")
async def handle_ui_home(callback: CallbackQuery) -> None:
    try:
        _seed_ui_anchor(callback)
        user = callback.from_user
        _clear_picker_awaiting_custom(user.id)
        usage = await get_usage_summary(db, user.id)
        plan = str(usage.get("plan") or db.get_plan(user.id))
        min_level, _ = db.get_user_settings(user.id)
        effective_min_level = "MEDIUM" if plan == "FREE" else min_level
        usage["min_level"] = str(usage.get("min_level") or effective_min_level)
        text = home_text(user, plan, usage)
        await _edit_or_send(callback, text, reply_markup=home_kb(plan))
    finally:
        await callback.answer()


@router.callback_query(F.data == "ui:test_lead")
async def handle_ui_test_lead(callback: CallbackQuery) -> None:
    try:
        _seed_ui_anchor(callback)
        if isinstance(callback.message, Message):
            await handle_test_lead(callback.message)
        else:
            await callback.bot.send_message(callback.from_user.id, "Use /test_lead to run the test.")
    finally:
        await callback.answer()


@router.callback_query(F.data == "ui:usage")
async def handle_ui_usage(callback: CallbackQuery) -> None:
    try:
        _seed_ui_anchor(callback)
        user_id = callback.from_user.id
        _clear_picker_awaiting_custom(user_id)
        usage = await get_usage_summary(db, user_id)
        plan = str(usage.get("plan") or db.get_plan(user_id))
        min_level = str(usage.get("min_level") or "—")

        today_sent = _value_or_dash(usage.get("today_sent"))
        today_blocked = _value_or_dash(usage.get("today_blocked"))
        week_sent = _value_or_dash(usage.get("week_sent"))
        week_blocked = _value_or_dash(usage.get("week_blocked"))
        daily_limit_text = _daily_limit_text(usage.get("daily_limit"), plan)

        lines = [
            f"Plan: {plan}",
            f"Min level: {min_level}",
            f"Today: {today_sent} sent / {today_blocked} blocked • Daily limit: {daily_limit_text}",
            f"Last 7 days: {week_sent} sent / {week_blocked} blocked",
        ]
        if plan == "FREE":
            lines.append("Upgrade to PRO for unlimited daily leads and LOW matches.")
        elif today_sent == "—" and today_blocked == "—" and week_sent == "—" and week_blocked == "—":
            lines.append("No usage data yet.")
        text = "\n".join(lines)
        await _edit_or_send(callback, text, reply_markup=back_home_kb())
    finally:
        await callback.answer()


@router.callback_query(F.data == "ui:upgrade")
async def handle_ui_upgrade(callback: CallbackQuery) -> None:
    try:
        _seed_ui_anchor(callback)
        user = callback.from_user
        _clear_picker_awaiting_custom(user.id)
        log_event(
            "upgrade_clicked",
            user_id=user.id,
            plan=db.get_plan(user.id),
            meta={"source": "ui:upgrade"},
        )
        checkout_url: str | None = None
        session_id: str | None = None
        subscription_id: str | None = None
        try:
            session = create_checkout_session(
                user_id=user.id,
                username=user.username,
                request_id=None,
            )
            maybe_url = session.get("url")
            if isinstance(maybe_url, str) and maybe_url.strip():
                checkout_url = maybe_url.strip()
            raw_session_id = session.get("id")
            if isinstance(raw_session_id, str) and raw_session_id.strip():
                session_id = raw_session_id.strip()
            raw_subscription_id = session.get("subscription")
            if isinstance(raw_subscription_id, str) and raw_subscription_id.strip():
                subscription_id = raw_subscription_id.strip()
        except Exception:
            checkout_url = None

        if checkout_url:
            log_event(
                "checkout_created",
                user_id=user.id,
                plan=db.get_plan(user.id),
                meta={"source": "ui:upgrade", "checkout_session_id": session_id},
            )
            log_event(
                "checkout_presented",
                user_id=user.id,
                plan=db.get_plan(user.id),
                meta={
                    "source": "ui:upgrade",
                    "checkout_session_id": session_id,
                    "subscription_id": subscription_id,
                },
            )
            log_event(
                "checkout_opened",
                user_id=user.id,
                plan=db.get_plan(user.id),
                meta={"source": "ui:upgrade", "inferred": "presented"},
            )
            text = (
                "Upgrade to PRO in one click.\n"
                "Use secure checkout to unlock LOW leads and higher limits.\n"
                "After payment, tap Return to Telegram to come back here."
            )
            await _edit_or_send(callback, text, reply_markup=_upgrade_checkout_kb(checkout_url))
        else:
            await _edit_or_send(callback, render_upgrade_text(), reply_markup=_upgrade_back_kb())
    finally:
        await callback.answer()


@router.callback_query(F.data == "ui:help")
async def handle_ui_help(callback: CallbackQuery) -> None:
    try:
        _seed_ui_anchor(callback)
        _clear_picker_awaiting_custom(callback.from_user.id)
        await _edit_or_send(callback, _help_text(), reply_markup=back_home_kb())
    finally:
        await callback.answer()

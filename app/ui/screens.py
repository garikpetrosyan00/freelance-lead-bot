"""Reusable UI texts and inline keyboards for guided menu flow."""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User


def welcome_text() -> str:
    return "Welcome to Freelance Lead Bot. Tap the button below to open the menu."


def _fmt_usage_value(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    return "—"


def _fmt_daily_limit(value: Any, plan: str) -> str:
    if isinstance(value, int):
        return str(value)
    if plan == "PRO":
        return "Unlimited"
    return "—"


def home_text(user: User, plan: str, usage_summary: dict[str, Any] | None) -> str:
    summary = usage_summary or {}
    plan_upper = (plan or "").upper().strip()
    plan_label = "🆓 FREE" if plan_upper == "FREE" else "💼 PRO" if plan_upper == "PRO" else f"📦 {plan_upper or '—'}"
    min_skill_matches = summary.get("min_skill_matches")
    min_skill_matches_text = str(min_skill_matches) if isinstance(min_skill_matches, int) else "—"
    today_sent = _fmt_usage_value(summary.get("today_sent"))
    today_blocked = _fmt_usage_value(summary.get("today_blocked"))
    daily_limit = _fmt_daily_limit(summary.get("daily_limit"), plan_upper)
    lines = [
        "🏠 Home Dashboard",
        f"Plan: {plan_label}",
        f"Match filter: {min_skill_matches_text} skills",
        f"Daily lead limit: {daily_limit}",
        f"Today usage: {today_sent} sent / {today_blocked} blocked",
    ]
    return "\n".join(lines)


def start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Start / Open menu",
                    callback_data="ui:home",
                )
            ]
        ]
    )


def home_kb(plan: str) -> InlineKeyboardMarkup:
    plan_upper = (plan or "").upper().strip()
    if plan_upper == "PRO":
        rows = [
            [
                InlineKeyboardButton(text="⚙️ Settings", callback_data="ui:settings"),
                InlineKeyboardButton(text="📌 Skills", callback_data="ui:skills"),
            ],
            [
                InlineKeyboardButton(text="🔎 Test lead", callback_data="ui:test_lead"),
            ],
            [
                InlineKeyboardButton(text="🎯 Match Filter", callback_data="ui:match_filter"),
            ],
            [
                InlineKeyboardButton(text="📊 Usage", callback_data="ui:usage"),
                InlineKeyboardButton(text="ℹ️ Help", callback_data="ui:help"),
            ],
        ]
    else:
        rows = [
            [
                InlineKeyboardButton(text="💳 Upgrade", callback_data="ui:upgrade"),
                InlineKeyboardButton(text="⚙️ Settings", callback_data="ui:settings"),
            ],
            [
                InlineKeyboardButton(text="📌 Skills", callback_data="ui:skills"),
                InlineKeyboardButton(text="🔎 Test lead", callback_data="ui:test_lead"),
            ],
            [
                InlineKeyboardButton(text="🎯 Match Filter", callback_data="ui:match_filter"),
            ],
            [
                InlineKeyboardButton(text="📊 Usage", callback_data="ui:usage"),
                InlineKeyboardButton(text="ℹ️ Help", callback_data="ui:help"),
            ],
        ]
    _assert_unique_home_buttons(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _assert_unique_home_buttons(rows: list[list[InlineKeyboardButton]]) -> None:
    seen: set[str] = set()
    for row in rows:
        for button in row:
            key = f"{button.callback_data}|{button.text}"
            if key in seen:
                raise AssertionError(f"Duplicate home button detected: {key}")
            seen.add(key)


def back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back", callback_data="ui:home")],
        ]
    )

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


def _fmt_cap(value: Any, plan: str) -> str:
    if isinstance(value, int):
        return str(value)
    if plan == "PRO":
        return "unlimited"
    return "—"


def home_text(user: User, plan: str, usage_summary: dict[str, Any] | None) -> str:
    summary = usage_summary or {}
    plan_upper = (plan or "").upper().strip()
    plan_label = "🆓 FREE" if plan_upper == "FREE" else "💼 PRO" if plan_upper == "PRO" else f"📦 {plan_upper or '—'}"
    min_level = str(summary.get("min_level") or "—").upper()
    today_sent = _fmt_usage_value(summary.get("today_sent"))
    today_blocked = _fmt_usage_value(summary.get("today_blocked"))
    cap = _fmt_cap(summary.get("cap"), plan)
    lines = [
        "🏠 Home Dashboard",
        f"Plan: {plan_label}",
        f"Min level: {min_level}",
        f"Cap: {cap}",
        f"Today usage: {today_sent} sent / {today_blocked} blocked",
    ]
    if plan_upper == "PRO" and min_level == "LOW":
        lines.append("🚀 Maximum lead coverage enabled")
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
    _ = plan  # Kept for future plan-specific variations without changing call sites.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Upgrade", callback_data="ui:upgrade"),
                InlineKeyboardButton(text="⚙️ Settings", callback_data="ui:settings"),
            ],
            [
                InlineKeyboardButton(text="📌 Skills", callback_data="ui:skills"),
                InlineKeyboardButton(text="🔎 Test lead", callback_data="ui:test_lead"),
            ],
            [
                InlineKeyboardButton(text="📊 Usage", callback_data="ui:usage"),
                InlineKeyboardButton(text="💳 Upgrade", callback_data="ui:upgrade"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Settings", callback_data="ui:settings"),
                InlineKeyboardButton(text="ℹ️ Help", callback_data="ui:help"),
            ],
        ]
    )


def back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Back", callback_data="ui:home")],
        ]
    )

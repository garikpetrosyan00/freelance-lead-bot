"""UI text and keyboard builders for button-based settings."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User


def _daily_limit_label(daily_limit: int | None) -> str:
    if daily_limit is None:
        return "unlimited"
    return str(daily_limit)


def settings_text(
    user: User,
    plan: str,
    min_level: str,
    daily_limit: int | None,
    *,
    saved: bool = False,
    locked_hint: bool = False,
) -> str:
    first_name = (user.first_name or "there").strip()[:40]
    safe_name = first_name or "there"
    text = (
        f"Settings for {safe_name}\n"
        f"Plan: {plan}\n"
        f"Min match level: {min_level}\n"
        f"Daily lead limit: {_daily_limit_label(daily_limit)}"
    )
    if saved:
        text = f"{text}\n\n✅ Saved"
    elif locked_hint:
        text = f"{text}\n\n🔒 Available in PRO"
    return text


def _min_btn(current: str, level: str) -> InlineKeyboardButton:
    prefix = "✅ " if current == level else ""
    return InlineKeyboardButton(text=f"{prefix}Min: {level}", callback_data=f"set:min:{level}")


def settings_kb(plan: str, min_level: str, daily_limit: int | None) -> InlineKeyboardMarkup:
    _ = daily_limit
    rows = [
        [
            _min_btn(min_level, "LOW"),
            _min_btn(min_level, "MEDIUM"),
            _min_btn(min_level, "HIGH"),
        ],
    ]
    if plan == "FREE":
        rows.append([InlineKeyboardButton(text="💳 Upgrade", callback_data="ui:upgrade")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="ui:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

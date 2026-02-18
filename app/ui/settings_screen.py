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
    min_skill_matches: int,
    daily_limit: int | None,
    *,
    saved: bool = False,
) -> str:
    first_name = (user.first_name or "there").strip()[:40]
    safe_name = first_name or "there"
    text = (
        f"Settings for {safe_name}\n"
        f"Plan: {plan}\n"
        f"Minimum skill matches: {int(min_skill_matches)}\n"
        f"Daily lead limit: {_daily_limit_label(daily_limit)}"
    )
    if saved:
        text = f"{text}\n\n✅ Saved"
    return text


def _min_btn(current: int, value: int) -> InlineKeyboardButton:
    prefix = "✅ " if int(current) == int(value) else ""
    return InlineKeyboardButton(text=f"{prefix}{value}", callback_data=f"set:min_skill_matches:{value}")


def settings_kb(plan: str, min_skill_matches: int, daily_limit: int | None) -> InlineKeyboardMarkup:
    _ = (plan, daily_limit)
    rows = [
        [
            _min_btn(min_skill_matches, 3),
            _min_btn(min_skill_matches, 4),
            _min_btn(min_skill_matches, 5),
        ],
        [
            _min_btn(min_skill_matches, 6),
            _min_btn(min_skill_matches, 7),
            _min_btn(min_skill_matches, 8),
        ],
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="ui:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

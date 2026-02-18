"""UI text and keyboard builders for button-based settings."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User

from app.db import min_skill_matches_bounds_for_skills_count


def _daily_limit_label(daily_limit: int | None) -> str:
    if daily_limit is None:
        return "unlimited"
    return str(daily_limit)


def settings_text(
    user: User,
    plan: str,
    min_skill_matches: int,
    daily_limit: int | None,
    skills_count: int,
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
    min_allowed, max_allowed = min_skill_matches_bounds_for_skills_count(skills_count)
    if saved:
        text = f"{text}\n\n✅ Saved"
    if min_allowed == max_allowed:
        text = (
            f"{text}\n\n"
            f"You currently have {int(skills_count)} skills selected.\n"
            "Add more skills to increase this value."
        )
    return text


def _min_btn(current: int, value: int) -> InlineKeyboardButton:
    prefix = "✅ " if int(current) == int(value) else ""
    return InlineKeyboardButton(text=f"{prefix}{value}", callback_data=f"set:min_skill_matches:{value}")


def settings_kb(plan: str, min_skill_matches: int, daily_limit: int | None, skills_count: int) -> InlineKeyboardMarkup:
    _ = (plan, daily_limit)
    rows: list[list[InlineKeyboardButton]] = []
    min_allowed, max_allowed = min_skill_matches_bounds_for_skills_count(skills_count)
    if min_allowed != max_allowed:
        buttons = [_min_btn(min_skill_matches, value) for value in range(min_allowed, max_allowed + 1)]
        row_size = 5
        for idx in range(0, len(buttons), row_size):
            rows.append(buttons[idx : idx + row_size])
    else:
        rows.append([InlineKeyboardButton(text="📌 Skills", callback_data="ui:skills")])
    rows.append(
        [
            InlineKeyboardButton(text="📩 Support", callback_data="ui:support"),
            InlineKeyboardButton(text="⬅️ Back", callback_data="ui:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

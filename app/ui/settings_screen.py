"""UI text and keyboard builders for button-based settings."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User


def _cap_label(cap: int | None) -> str:
    if cap is None:
        return "unlimited"
    return str(cap)


def settings_text(
    user: User,
    plan: str,
    min_level: str,
    cap: int | None,
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
        f"Daily cap: {_cap_label(cap)}"
    )
    if saved:
        text = f"{text}\n\n✅ Saved"
    elif locked_hint:
        text = f"{text}\n\n🔒 Available in PRO"
    return text


def _min_btn(current: str, level: str) -> InlineKeyboardButton:
    prefix = "✅ " if current == level else ""
    return InlineKeyboardButton(text=f"{prefix}Min: {level}", callback_data=f"set:min:{level}")


def _cap_btn(current: int | None, value: int | None, label: str) -> InlineKeyboardButton:
    selected = (current is None and value is None) or (current == value)
    prefix = "✅ " if selected else ""
    cb_value = "unlimited" if value is None else str(value)
    return InlineKeyboardButton(text=f"{prefix}Cap: {label}", callback_data=f"set:cap:{cb_value}")


def settings_kb(plan: str, min_level: str, cap: int | None) -> InlineKeyboardMarkup:
    _ = plan  # callbacks decide lock behavior
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _min_btn(min_level, "LOW"),
                _min_btn(min_level, "MEDIUM"),
                _min_btn(min_level, "HIGH"),
            ],
            [
                _cap_btn(cap, 5, "5"),
                _cap_btn(cap, 10, "10"),
                _cap_btn(cap, 20, "20"),
            ],
            [
                _cap_btn(cap, 50, "50"),
                _cap_btn(cap, None, "∞"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Back", callback_data="ui:home"),
            ],
        ]
    )

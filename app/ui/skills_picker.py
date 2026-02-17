"""Inline multi-select skills picker UI."""

from __future__ import annotations

from typing import TypedDict

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.skills_catalog import SKILLS

PAGE_SIZE = 10


class PickerState(TypedDict):
    all: list[str]
    selected: set[str]
    page: int
    message_id: int | None
    awaiting_custom: bool


ACTIVE_SKILL_PICKERS: dict[int, PickerState] = {}


def normalize_skill(s: str) -> str:
    return " ".join((s or "").strip().split())


def skills_picker_text(selected_count: int) -> str:
    return (
        "Select your skills (tap to toggle). ✅ Done when finished.\n"
        f"Selected: {selected_count}"
    )


def _total_pages(items_count: int) -> int:
    if items_count <= 0:
        return 1
    return ((items_count - 1) // PAGE_SIZE) + 1


def _skills_for_user(user_id: int) -> list[str]:
    state = ACTIVE_SKILL_PICKERS.get(user_id)
    if not state:
        return []
    return state["all"]


def _safe_page(user_id: int) -> int:
    state = ACTIVE_SKILL_PICKERS.get(user_id)
    if not state:
        return 0
    all_skills = _skills_for_user(user_id)
    pages = _total_pages(len(all_skills))
    page = int(state.get("page", 0))
    if page < 0:
        return 0
    if page >= pages:
        return pages - 1
    return page


def skills_picker_kb(user_id: int) -> InlineKeyboardMarkup:
    state = ACTIVE_SKILL_PICKERS.get(user_id)
    if not state:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✖ Cancel", callback_data="skill:cancel")]
            ]
        )

    all_skills = state["all"]
    selected = state["selected"]
    page = _safe_page(user_id)
    state["page"] = page

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = all_skills[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    for offset, skill in enumerate(page_items):
        idx = start + offset
        checked = skill.lower() in selected
        prefix = "✅" if checked else "⬜️"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix} {skill}",
                    callback_data=f"skill:toggle:{idx}",
                )
            ]
        )

    pages = _total_pages(len(all_skills))
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data="skill:prev"))
    if page + 1 < pages:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data="skill:next"))
    if nav_row:
        rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(text="➕ Add skill", callback_data="skill:add"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="✅ Done", callback_data="skill:done"),
            InlineKeyboardButton(text="✖ Cancel", callback_data="skill:cancel"),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def init_picker_state(user_id: int, current_skills: list[str]) -> PickerState:
    catalog: list[str] = []
    seen_catalog: set[str] = set()
    for raw in SKILLS:
        cleaned = normalize_skill(raw)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen_catalog:
            continue
        seen_catalog.add(key)
        catalog.append(cleaned)

    selected = {normalize_skill(skill).lower() for skill in current_skills if normalize_skill(skill)}
    extras: list[str] = []
    seen = {skill.lower() for skill in catalog}
    for raw in current_skills:
        cleaned = normalize_skill(raw)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        extras.append(cleaned)

    state: PickerState = {
        "all": catalog + extras,
        "selected": selected,
        "page": 0,
        "message_id": None,
        "awaiting_custom": False,
    }
    ACTIVE_SKILL_PICKERS[user_id] = state
    return state

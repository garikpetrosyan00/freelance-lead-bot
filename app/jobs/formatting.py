"""Shared lead/job message formatting."""

from __future__ import annotations

import textwrap

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _trim_summary(summary: str, *, max_lines: int = 3, max_line_chars: int = 96) -> str:
    raw = (summary or "").strip()
    if not raw:
        return "-"

    chunks: list[str] = []
    for paragraph in raw.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        wrapped = textwrap.wrap(
            paragraph,
            width=max_line_chars,
            break_long_words=False,
            break_on_hyphens=False,
        )
        if wrapped:
            chunks.extend(wrapped)

    if not chunks:
        return "-"

    trimmed = chunks[: max(2, min(4, max_lines))]
    if len(chunks) > len(trimmed):
        trimmed[-1] = trimmed[-1].rstrip(". ") + "…"
    return "\n".join(trimmed)


def format_lead_message(
    title: str,
    summary: str,
    url: str | None,
    matched_skills: list[str] | tuple[str, ...],
    source: str,
) -> tuple[str, InlineKeyboardMarkup | None]:
    clean_url = (url or "").strip()
    has_url = clean_url.startswith(("http://", "https://"))

    clean_skills = [str(skill).strip() for skill in matched_skills if str(skill).strip()]
    if clean_skills:
        matched_line = f"Matched skills: {', '.join(clean_skills[:10])}"
    else:
        matched_line = "Matched: 0 skills"

    lines = [
        "🔥 New lead found!",
        f"Title: {(title or 'Untitled').strip()}",
        f"Summary:\n{_trim_summary(summary)}",
        f"Link: {clean_url}" if has_url else None,
        matched_line,
        f"Source: {(source or '-').strip()}",
    ]
    text = "\n".join([line for line in lines if line])

    if has_url:
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Open job", url=clean_url)]]
        )
    else:
        reply_markup = None
    return text, reply_markup

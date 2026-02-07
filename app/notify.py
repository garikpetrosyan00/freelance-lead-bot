"""Notification helpers."""

from __future__ import annotations

from aiogram import Bot

from app.leads import Lead


def _format_line(label: str, value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return f"{label}: {value}"


async def send_lead(bot: Bot, user_id: int, lead: Lead, match: dict) -> None:
    matched = match.get("matched", [])
    matched_text = ", ".join(matched) if matched else "None"

    lines = [
        "🔥 New lead found!",
        f"Title: {lead.title}",
        _format_line("Budget", lead.budget),
        f"Match: {match.get('level', 'NONE')} ({match.get('score', 0)}%)",
        f"Matched skills: {matched_text}",
        f"Source: {lead.source}",
        _format_line("Link", lead.url),
    ]

    text = "\n".join([line for line in lines if line])
    await bot.send_message(chat_id=user_id, text=text)

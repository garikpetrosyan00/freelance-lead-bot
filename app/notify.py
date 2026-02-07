"""Notification helpers."""

from __future__ import annotations

import logging

from aiogram import Bot

from app.db import get_daily_usage, get_plan, increment_daily_usage, utc_day
from app.gating import can_send_notification
from app.leads import Lead


def _format_line(label: str, value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return f"{label}: {value}"


async def send_lead(bot: Bot, user_id: int, lead: Lead, match: dict) -> bool:
    plan = get_plan(user_id)
    match_level = match.get("level", "NONE")
    day = utc_day()
    sent_today = get_daily_usage(user_id, day)
    allowed, reason = can_send_notification(plan, match_level, sent_today)
    if not allowed:
        logging.getLogger(__name__).info(
            "Notification blocked: user=%s reason=%s plan=%s level=%s",
            user_id,
            reason,
            plan,
            match_level,
        )
        return False

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
    increment_daily_usage(user_id, day, by=1)
    return True

"""Notification helpers."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from aiogram import Bot

from app.analytics import log_event
from app.db import (
    get_daily_usage,
    get_last_sent_at,
    get_plan,
    get_user_settings,
    has_seen_lead,
    increment_daily_usage,
    mark_seen_lead,
    set_last_sent_at,
    utc_day,
)
from app.gating import can_send_notification, effective_cap
from app.leads import Lead


def _format_line(label: str, value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return f"{label}: {value}"


def _lead_hash(lead: Lead) -> str:
    fingerprint = f"{lead.title}\n{lead.description[:400]}"
    normalized = " ".join(fingerprint.lower().split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


async def send_lead(bot: Bot, user_id: int, lead: Lead, match: dict) -> bool:
    lead_hash = _lead_hash(lead)
    plan = get_plan(user_id)
    if has_seen_lead(user_id, lead_hash):
        logging.getLogger(__name__).info(
            "Notification blocked: user=%s reason=duplicate", user_id
        )
        log_event(
            "lead_blocked",
            user_id=user_id,
            lead_id=lead_hash,
            plan=plan,
            meta={"reason": "dedupe", "source": lead.source},
        )
        return False

    now = datetime.now(timezone.utc)
    last_sent_at = get_last_sent_at(user_id)
    cooldown_seconds = 30 if plan == "PRO" else 120
    if last_sent_at:
        try:
            last_dt = datetime.fromisoformat(last_sent_at)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if (now - last_dt).total_seconds() < cooldown_seconds:
                logging.getLogger(__name__).info(
                    "Notification blocked: user=%s reason=cooldown plan=%s",
                    user_id,
                    plan,
                )
                log_event(
                    "lead_blocked",
                    user_id=user_id,
                    lead_id=lead_hash,
                    plan=plan,
                    meta={"reason": "cooldown", "source": lead.source},
                )
                return False
        except ValueError:
            pass

    match_level = match.get("level", "NONE")
    day = utc_day()
    sent_today = get_daily_usage(user_id, day)
    min_level, user_cap = get_user_settings(user_id)
    cap = effective_cap(plan, user_cap)
    allowed, reason = can_send_notification(
        plan, match_level, sent_today, min_level, cap
    )
    if not allowed:
        logging.getLogger(__name__).info(
            "Notification blocked: user=%s reason=%s plan=%s level=%s",
            user_id,
            reason,
            plan,
            match_level,
        )
        blocked_reason = (
            "cap"
            if reason == "daily_cap_reached"
            else "free_limit"
            if reason == "level_not_allowed" and plan == "FREE"
            else reason
        )
        log_event(
            "lead_blocked",
            user_id=user_id,
            lead_id=lead_hash,
            plan=plan,
            meta={"reason": blocked_reason, "source": lead.source},
        )
        return False

    matched = match.get("matched", [])
    matched_text = ", ".join(matched) if matched else "None"

    lines = [
        "🔥 New lead found!",
        f"Title: {lead.title}",
        _format_line("Budget", lead.budget),
        f"⭐ Score: {match.get('score', 0)}%",
        f"Match: {match.get('level', 'NONE')} ({match.get('score', 0)}%)",
        f"Matched skills: {matched_text}",
        "🧠 Why:",
    ]

    details = match.get("details") or []
    for detail in details[:3]:
        lines.append(f"- {detail}")

    lines.extend(
        [
            f"Source: {lead.source}",
            _format_line("Link", lead.url),
        ]
    )

    text = "\n".join([line for line in lines if line])
    await bot.send_message(chat_id=user_id, text=text)
    increment_daily_usage(user_id, day, by=1)
    set_last_sent_at(user_id, now.isoformat())
    mark_seen_lead(user_id, lead_hash)
    log_event(
        "lead_sent",
        user_id=user_id,
        lead_id=lead_hash,
        match_level=str(match.get("level") or ""),
        score=int(match.get("score") or 0),
        plan=plan,
        meta={"source": lead.source},
    )
    return True

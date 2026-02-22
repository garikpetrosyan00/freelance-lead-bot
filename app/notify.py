"""Notification helpers."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from aiogram import Bot

from app.analytics import log_event
from app import db as db_module
from app.db import (
    get_daily_usage,
    get_last_sent_at,
    get_plan,
    get_user_settings,
    has_seen_lead,
    increment_daily_usage,
    mark_seen_lead,
    record_error,
    set_last_sent_at,
    utc_day,
)
from app.gating import can_send_notification, effective_cap
from app.jobs.formatting import format_lead_message
from app.leads import Lead
from app.monetization.teaser import maybe_send_teaser
from app.ops.logging_utils import log_kv, mask_user_id, safe_exc


def _lead_hash(lead: Lead) -> str:
    fingerprint = f"{lead.title}\n{lead.description[:400]}"
    normalized = " ".join(fingerprint.lower().split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


async def send_lead(bot: Bot, user_id: int, lead: Lead, match: dict) -> bool:
    lead_hash = _lead_hash(lead)
    plan = get_plan(user_id)
    if has_seen_lead(user_id, lead_hash):
        log_kv(
            logging.getLogger(__name__),
            logging.INFO,
            "Notification blocked",
            user=mask_user_id(user_id),
            reason="duplicate",
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
                    mask_user_id(user_id),
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

    match_level = str(match.get("level", "NONE"))
    overlap_count = len(match.get("matched") or [])
    day = utc_day()
    sent_today = get_daily_usage(user_id, day)
    min_skill_matches, _ = get_user_settings(user_id)
    cap = effective_cap(plan, None)
    allowed, reason = can_send_notification(
        plan, overlap_count, sent_today, min_skill_matches, cap
    )
    if not allowed:
        log_kv(
            logging.getLogger(__name__),
            logging.INFO,
            "Notification blocked",
            user=mask_user_id(user_id),
            reason=reason,
            plan=plan,
            overlap=overlap_count,
        )
        blocked_reason = (
            "cap"
            if reason == "daily_cap_reached"
            else "min_skill_matches"
            if reason == "below_min_skill_matches"
            else reason
        )
        log_event(
            "lead_blocked",
            user_id=user_id,
            lead_id=lead_hash,
            plan=plan,
            meta={"reason": blocked_reason, "source": lead.source},
        )
        teaser_reason: str | None = None
        if plan == "FREE":
            if reason == "daily_cap_reached":
                teaser_reason = "cap"
            elif reason == "below_min_skill_matches":
                teaser_reason = "min_skill_matches"
        if teaser_reason is not None:
            await maybe_send_teaser(
                bot=bot,
                db=db_module,
                user_id=user_id,
                chat_id=user_id,
                lead=lead,
                reason=teaser_reason,
                match_level=str(match_level or "NONE"),
                required_matches=int(min_skill_matches),
                found_matches=int(overlap_count),
            )
        return False

    matched = list(match.get("matched") or [])
    text, reply_markup = format_lead_message(
        lead.title,
        lead.description,
        lead.url,
        matched,
        lead.source,
    )
    try:
        await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    except Exception as exc:
        record_error(
            "notify",
            exc,
            context={
                "user_id": user_id,
                "source": lead.source,
                "lead_hash": lead_hash,
                "action": "send_lead",
            },
        )
        log_kv(
            logging.getLogger(__name__),
            logging.WARNING,
            "Notification send failed",
            user=mask_user_id(user_id),
            error=safe_exc(exc),
        )
        return False
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

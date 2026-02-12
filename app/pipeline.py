"""Fake ingestion pipeline for leads."""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from aiogram import Bot

from app.analytics import lead_id_from_lead, log_event
from app.db import get_plan, get_skills, list_subscribed_users
from app.leads import Lead
from app.matching import match_lead
from app.notify import send_lead

logger = logging.getLogger(__name__)


def _fake_leads() -> Iterable[Lead]:
    return [
        Lead(
            title="Python Django API",
            description="Need a Django developer to build a REST API.",
            budget="$1k-$3k",
            source="test",
            url=None,
        ),
        Lead(
            title="React dashboard build",
            description="Looking for a React engineer for a data dashboard.",
            budget="$2k-$5k",
            source="test",
            url=None,
        ),
        Lead(
            title="Figma redesign",
            description="UI redesign in Figma for a SaaS product.",
            budget="$800-$1.5k",
            source="test",
            url=None,
        ),
    ]


async def _dispatch_lead(bot: Bot, lead: Lead) -> int:
    notified = 0
    lead_id = lead_id_from_lead(lead)
    user_ids = list_subscribed_users()
    for user_id in user_ids:
        try:
            skills = get_skills(user_id)
            result = match_lead(skills, lead)
            if result.get("level") == "NONE":
                continue
            plan = get_plan(user_id)
            log_event(
                "lead_matched",
                user_id=user_id,
                lead_id=lead_id,
                match_level=str(result.get("level") or ""),
                score=int(result.get("score") or 0),
                plan=plan,
                meta={"source": lead.source},
            )
            if await send_lead(bot, user_id, lead, result):
                notified += 1
        except Exception:
            logger.exception("Failed to notify user %s", user_id)
    return notified


async def run_fake_ingestion(bot: Bot) -> None:
    leads = list(_fake_leads())
    index = 0
    while True:
        lead = leads[index % len(leads)]
        index += 1
        lead_id = lead_id_from_lead(lead)
        log_event(
            "lead_ingested",
            lead_id=lead_id,
            meta={"source": lead.source, "budget": lead.budget, "has_link": bool(lead.url)},
        )
        logger.info("Generated fake lead: %s", lead.title)
        notified = await _dispatch_lead(bot, lead)
        logger.info("Sent lead to %s users", notified)
        await asyncio.sleep(60)


async def run_once(bot: Bot) -> int:
    lead = Lead(
        title="Full-stack demo lead",
        description="Looking for Python, Django, and React experience.",
        budget="$1k-$2k",
        source="test",
        url=None,
    )
    lead_id = lead_id_from_lead(lead)
    log_event(
        "lead_ingested",
        lead_id=lead_id,
        meta={"source": lead.source, "budget": lead.budget, "has_link": bool(lead.url)},
    )
    logger.info("Manual lead trigger: %s", lead.title)
    return await _dispatch_lead(bot, lead)

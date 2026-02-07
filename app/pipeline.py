"""Fake ingestion pipeline for leads."""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from aiogram import Bot

from app.db import get_skills, list_subscribed_users
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
    user_ids = list_subscribed_users()
    for user_id in user_ids:
        try:
            skills = get_skills(user_id)
            result = match_lead(skills, lead)
            if result.get("level") == "NONE":
                continue
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
    logger.info("Manual lead trigger: %s", lead.title)
    return await _dispatch_lead(bot, lead)

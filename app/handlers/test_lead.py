"""/test_lead command handler."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import get_upwork_rss_min_matched_skills
from app.db import get_skills
from app.jobs.formatting import format_lead_message
from app.leads import Lead
from app.matching import match_lead

router = Router()


async def send_test_lead_preview(
    *,
    user_id: int,
    answer,
    include_all: bool = False,
) -> None:
    user_skills = get_skills(user_id)
    if not user_skills:
        await answer("No skills set yet. Use /set_skills ...")
        return
    min_matched_skills = max(1, get_upwork_rss_min_matched_skills())

    mode = "ALL (debug)" if include_all else f"FILTERED (min matched skills: {min_matched_skills})"
    await answer(
        "🔎 Test lead preview (debug tool)\n"
        f"Mode: {mode}\n"
        "Real Upwork jobs are pushed automatically when your RSS feeds publish new items."
    )

    leads = [
        Lead(
            title="Build a Django API with React frontend",
            description="Looking for a developer to build a Python Django backend and React UI.",
            budget="$2k-$4k",
            source="test",
            url="https://www.upwork.com/jobs/~0123456789abcdef",
        ),
        Lead(
            title="Java Spring Boot microservices",
            description="Need help with Java, Spring, and microservices architecture.",
            budget="$5k+",
            source="test",
            url="https://www.upwork.com/jobs/~0abcdef123456789",
        ),
        Lead(
            title="UI/UX refresh for SaaS dashboard",
            description="Seeking a designer skilled in Figma and modern UI design.",
            budget="$1k-$2k",
            source="test",
            url="https://www.upwork.com/nx/search/jobs/",
        ),
    ]

    sent = 0
    for idx, lead in enumerate(leads, start=1):
        result = match_lead(user_skills, lead)
        matched = result["matched"]
        if not include_all and len(matched) < min_matched_skills:
            continue

        display_skills = matched if matched else ["-"]
        text, reply_markup = format_lead_message(
            lead.title,
            lead.description,
            lead.url,
            display_skills,
            lead.source,
        )
        await answer(f"🔎 Test lead {idx}/{len(leads)}\n{text}", reply_markup=reply_markup)
        sent += 1

    if sent == 0:
        await answer(
            "No test leads passed the filter.\n"
            "Use /test_lead all to inspect unfiltered debug output."
        )


@router.message(Command("test_lead"))
async def handle_test_lead(message: Message) -> None:
    parts = (message.text or "").strip().split()
    include_all = len(parts) > 1 and parts[1].lower() == "all"
    await send_test_lead_preview(
        user_id=message.from_user.id,
        answer=message.answer,
        include_all=include_all,
    )

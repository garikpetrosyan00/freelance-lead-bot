"""/test_lead command handler."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db import get_skills
from app.leads import Lead
from app.matching import match_lead

router = Router()


@router.message(Command("test_lead"))
async def handle_test_lead(message: Message) -> None:
    user_skills = get_skills(message.from_user.id)
    if not user_skills:
        await message.answer("No skills set yet. Use /set_skills ...")
        return

    leads = [
        Lead(
            title="Build a Django API with React frontend",
            description="Looking for a developer to build a Python Django backend and React UI.",
            budget="$2k-$4k",
            source="test",
            url=None,
        ),
        Lead(
            title="Java Spring Boot microservices",
            description="Need help with Java, Spring, and microservices architecture.",
            budget="$5k+",
            source="test",
            url=None,
        ),
        Lead(
            title="UI/UX refresh for SaaS dashboard",
            description="Seeking a designer skilled in Figma and modern UI design.",
            budget="$1k-$2k",
            source="test",
            url=None,
        ),
    ]

    for idx, lead in enumerate(leads, start=1):
        result = match_lead(user_skills, lead)
        matched = result["matched"]
        matched_text = ", ".join(matched) if matched else "None"
        text = (
            f"🔎 Test lead {idx}/3\n"
            f"Title: {lead.title}\n"
            f"Match: {result['level']} ({result['score']}%)\n"
            f"Matched skills: {matched_text}\n"
            f"Source: {lead.source}"
        )
        await message.answer(text)

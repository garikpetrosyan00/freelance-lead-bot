"""Debug command for Upwork official API connectivity."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app import db
from app.integrations.upwork.client import search_public_jobs

router = Router()


def _parse_query(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        return None
    value = parts[1].strip()
    return value or None


def _default_query_for_user(user_id: int) -> str:
    profiles = db.upwork_profiles_list(user_id)
    for profile in profiles:
        if int(profile.get("is_enabled") or 0) == 1:
            query = str(profile.get("query") or "").strip()
            if query:
                return query
    return "react"


@router.message(Command("upwork_test_api"))
async def handle_upwork_test_api(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Unauthorized")
        return

    user_id = message.from_user.id
    account = db.upwork_oauth_get_account(user_id)
    if not account or int(account.get("is_connected") or 0) != 1:
        await message.answer("Not connected. Use /upwork_connect")
        return

    query_text = _parse_query(message.text or "") or _default_query_for_user(user_id)
    try:
        jobs = await search_public_jobs(user_id=user_id, query_text=query_text, limit=20)
    except Exception as exc:
        db.record_error(
            "upwork_api",
            exc,
            context={"user_id": user_id, "action": "upwork_test_api"},
        )
        await message.answer("Upwork API call failed. Check OAuth connection and try again.")
        return

    if not jobs:
        await message.answer("Upwork API OK. Found 0 jobs.")
        return

    first = jobs[0]
    title = str(first.get("title") or "Untitled")
    url = str(first.get("url") or "-")
    await message.answer(f"Upwork API OK. Found {len(jobs)} jobs. First: {title}\nLink: {url}")

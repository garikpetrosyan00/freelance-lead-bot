"""Debug command for Upwork public search parsing."""

from __future__ import annotations

import logging
import time

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app import db
from app.integrations.upwork.public_search import search_public_jobs_public

router = Router()
logger = logging.getLogger(__name__)
_COOLDOWN_SECONDS = 15.0
_LAST_CALL_BY_USER: dict[int, float] = {}


def _parse_query(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        return None
    value = parts[1].strip()
    if len(value) < 2 or len(value) > 80:
        return None
    return value


def _is_cooldown_active(user_id: int, now_mono: float) -> bool:
    last = _LAST_CALL_BY_USER.get(user_id)
    if last is None:
        return False
    return (now_mono - last) < _COOLDOWN_SECONDS


def _mark_cooldown(user_id: int, now_mono: float) -> None:
    _LAST_CALL_BY_USER[user_id] = now_mono
    if len(_LAST_CALL_BY_USER) > 2048:
        cutoff = now_mono - (_COOLDOWN_SECONDS * 4)
        stale = [uid for uid, ts in _LAST_CALL_BY_USER.items() if ts < cutoff]
        for uid in stale:
            _LAST_CALL_BY_USER.pop(uid, None)


@router.message(Command("upwork_test_public"))
async def handle_upwork_test_public(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Unauthorized")
        return
    user_id = message.from_user.id
    query = _parse_query(message.text or "")
    if not query:
        await message.answer("Usage: /upwork_test_public <query> (2..80 chars)")
        return

    now_mono = time.monotonic()
    if _is_cooldown_active(user_id, now_mono):
        await message.answer("Please wait a few seconds before running /upwork_test_public again.")
        return
    _mark_cooldown(user_id, now_mono)

    try:
        jobs = await search_public_jobs_public(query, limit=5)
    except Exception as exc:
        db.record_error(
            "upwork_api",
            exc,
            context={"user_id": user_id, "action": "upwork_test_public"},
        )
        await message.answer("Public Upwork search failed. Try again shortly.")
        return

    logger.info("upwork_test_public query=%r count=%s", query[:80], len(jobs))
    if not jobs:
        await message.answer("No jobs found (public). Try another query.")
        return

    lines = [f"Public Upwork: {len(jobs)} jobs (showing up to 3)"]
    for item in jobs[:3]:
        title = str(item.get("title") or "Untitled")
        published_at = str(item.get("published_at") or "").strip() or "unknown"
        url = str(item.get("url") or "").strip() or "-"
        lines.append(f"- {title}")
        lines.append(f"  {published_at}")
        lines.append(f"  {url}")
    await message.answer("\n".join(lines), disable_web_page_preview=True)


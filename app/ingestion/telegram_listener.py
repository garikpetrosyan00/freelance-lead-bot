"""Telegram group/channel ingestion via Telethon."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import deque
from typing import Deque, Dict, Iterable, Set

from aiogram import Bot
from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.config import get_tg_api_hash, get_tg_api_id, get_tg_source_chats
from app.db import get_skills, list_subscribed_users
from app.leads import Lead
from app.matching import match_lead
from app.notify import send_lead

logger = logging.getLogger(__name__)

SESSION_PATH = os.path.join("data", "telethon.session")
MAX_DEDUP_IDS = 500


def _extract_budget(text: str) -> str | None:
    match = re.search(
        r"([\$€]\s?\d{1,3}(?:[,\d]{0,6})?(?:\.\d+)?(?:\s?[kK])?)",
        text,
    )
    return match.group(1) if match else None


def _trim_first_line(text: str, max_len: int = 80) -> str:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    return (first_line[: max_len - 1] + "…") if len(first_line) > max_len else first_line


def _message_url(message: Message) -> str | None:
    if message.chat and getattr(message.chat, "username", None):
        return f"https://t.me/{message.chat.username}/{message.id}"
    if message.is_channel and message.chat_id:
        chat_id = int(message.chat_id)
        if str(chat_id).startswith("-100"):
            return f"https://t.me/c/{str(chat_id)[4:]}/{message.id}"
    return None


def _build_lead(text: str, message: Message) -> Lead:
    return Lead(
        title=_trim_first_line(text),
        description=text[:2000],
        budget=_extract_budget(text),
        source="telegram",
        url=_message_url(message),
    )


def _parse_chat_ids(chats: Iterable[str]) -> list[int | str]:
    parsed: list[int | str] = []
    for chat in chats:
        if chat.lstrip("-").isdigit():
            parsed.append(int(chat))
        else:
            parsed.append(chat)
    return parsed


def _is_duplicate(dedup: Dict[int, Deque[int]], chat_id: int, message_id: int) -> bool:
    # Dedup cache prevents double-processing on reconnect/resume.
    queue = dedup.setdefault(chat_id, deque(maxlen=MAX_DEDUP_IDS))
    if message_id in queue:
        return True
    queue.append(message_id)
    return False


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


async def run_telegram_listener(bot: Bot) -> None:
    chats = get_tg_source_chats()
    if not chats:
        logger.warning("TG_SOURCE_CHATS is empty. Telegram ingestion disabled.")
        return

    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    client = TelegramClient(SESSION_PATH, get_tg_api_id(), get_tg_api_hash())

    dedup: Dict[int, Deque[int]] = {}

    await client.start()
    logger.info("Telethon connected. Listening to chats: %s", ", ".join(chats))

    @client.on(events.NewMessage(chats=_parse_chat_ids(chats)))
    async def handler(event: events.NewMessage.Event) -> None:
        message = event.message
        if not message or not message.raw_text:
            return

        if message.chat_id is None:
            return

        if _is_duplicate(dedup, int(message.chat_id), int(message.id)):
            return

        text = message.raw_text.strip()
        if not text:
            return

        lead = _build_lead(text, message)
        logger.info("Telegram lead received: %s", lead.title)
        try:
            notified = await _dispatch_lead(bot, lead)
            logger.info("Telegram lead sent to %s users", notified)
        except Exception:
            logger.exception("Failed to dispatch telegram lead")

    try:
        await client.run_until_disconnected()
    finally:
        logger.info("Telethon listener stopped")

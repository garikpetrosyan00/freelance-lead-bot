"""Temporary incoming message debug middleware."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)


class IncomingDebugMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        text = (event.text or "").strip()
        user_id = event.from_user.id if event.from_user else None
        chat_type = event.chat.type if event.chat else "unknown"
        entities = []
        if event.entities:
            for entity in event.entities:
                entities.append(
                    {
                        "type": entity.type,
                        "offset": entity.offset,
                        "length": entity.length,
                    }
                )
        logger.info(
            "incoming message before handler user=%s chat=%s text=%r entities=%s",
            user_id,
            chat_type,
            text,
            entities,
        )
        result = await handler(event, data)
        logger.info("incoming message after handler user=%s text=%r", user_id, text)
        return result

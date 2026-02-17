"""In-memory state for single-message UI rendering."""

from __future__ import annotations

from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

UI_MESSAGE_ID: dict[int, int] = {}


async def render_ui_message(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    reply_markup=None,
    *,
    parse_mode: str | None = None,
) -> Message | None:
    message_id = UI_MESSAGE_ID.get(user_id)
    if message_id:
        try:
            edited = await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            if isinstance(edited, Message):
                return edited
        except TelegramBadRequest as exc:
            details = str(exc).lower()
            if "message is not modified" in details:
                with suppress(TelegramBadRequest):
                    await bot.edit_message_reply_markup(
                        chat_id=chat_id,
                        message_id=message_id,
                        reply_markup=reply_markup,
                    )
                return None
            if "message to edit not found" in details or "can't be edited" in details:
                pass
            else:
                pass
        except Exception:
            pass

    msg = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    UI_MESSAGE_ID[user_id] = msg.message_id
    return msg

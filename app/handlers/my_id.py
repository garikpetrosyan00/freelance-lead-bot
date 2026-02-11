"""/my_id command handler."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("my_id"))
async def handle_my_id(message: Message) -> None:
    user_id = message.from_user.id
    lines = [
        f"Your Telegram user id: {user_id}",
        "Send this to admin for PRO activation after payment.",
    ]
    await message.answer("\n".join(lines))

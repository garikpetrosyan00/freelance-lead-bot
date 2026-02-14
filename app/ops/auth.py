"""Authorization helpers for admin commands."""

from __future__ import annotations

import os

from aiogram.types import Message

from app.config import get_admin_user_ids, is_admin as config_is_admin


def is_admin(user_id: int) -> bool:
    return config_is_admin(user_id)


def get_super_admin_ids() -> set[int]:
    raw = os.getenv("SUPER_ADMIN_IDS", "").strip()
    if not raw:
        return get_admin_user_ids()
    ids: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if token.isdigit():
            ids.add(int(token))
    return ids or get_admin_user_ids()


async def require_admin(message: Message) -> bool:
    user = message.from_user
    if user is None or not is_admin(user.id):
        await message.answer("Unauthorized")
        return False
    return True


async def require_super_admin(message: Message) -> bool:
    user = message.from_user
    if user is None or user.id not in get_super_admin_ids():
        await message.answer("Unauthorized")
        return False
    return True

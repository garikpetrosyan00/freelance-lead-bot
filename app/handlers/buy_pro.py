"""Lemon Squeezy-powered PRO purchase command."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import get_lemon_checkout_url

router = Router()


def _build_lemon_checkout_url(base_url: str, telegram_user_id: int, username: str | None) -> str:
    split = urlsplit(base_url)
    query_pairs = parse_qsl(split.query, keep_blank_values=True)
    query_pairs.append(("checkout[custom][telegram_user_id]", str(telegram_user_id)))
    if username:
        query_pairs.append(("checkout[custom][username]", username))
    query = urlencode(query_pairs)
    return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))


@router.message(Command("buy_pro"))
async def handle_buy_pro(message: Message) -> None:
    user = message.from_user
    if user is None:
        await message.answer("Unable to identify user.")
        return

    base_checkout_url = get_lemon_checkout_url()
    if not base_checkout_url:
        await message.answer(
            "/buy_pro is temporarily unavailable. Payment link is not configured right now.\n"
            "Please contact support and try again soon."
        )
        return

    checkout_url = _build_lemon_checkout_url(
        base_url=base_checkout_url,
        telegram_user_id=user.id,
        username=user.username,
    )

    await message.answer(
        "Open this secure checkout URL to activate PRO:\n"
        f"{checkout_url}\n\n"
        "After payment, activation is automatic."
    )

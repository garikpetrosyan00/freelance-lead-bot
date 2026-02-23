"""Telegram commands for Upwork OAuth connect/disconnect."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import db
from app.config import get_public_base_url

router = Router()
_STATE_TTL_MINUTES = 10


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@router.message(Command("upwork_connect"))
async def handle_upwork_connect(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Unauthorized")
        return
    user_id = message.from_user.id
    account = db.upwork_oauth_get_account(user_id)
    if account and int(account.get("is_connected") or 0) == 1:
        await message.answer("Already connected. Use /upwork_disconnect to revoke.")
        return

    now = _utc_now()
    expires_at = now + timedelta(minutes=_STATE_TTL_MINUTES)
    state = secrets.token_urlsafe(32)
    db.upwork_oauth_state_create(
        state=state,
        user_id=user_id,
        created_at_iso=now.isoformat(),
        expires_at_iso=expires_at.isoformat(),
        redirect_context=None,
    )

    connect_url = f"{get_public_base_url()}/upwork/connect?state={state}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Connect Upwork", url=connect_url)],
        ]
    )
    await message.answer(
        "Open this link to connect your Upwork account:\n"
        f"{connect_url}\n\n"
        f"This link expires in {_STATE_TTL_MINUTES} minutes.",
        reply_markup=kb,
        disable_web_page_preview=True,
    )


@router.message(Command("upwork_disconnect"))
async def handle_upwork_disconnect(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Unauthorized")
        return
    now_iso = _utc_now().isoformat()
    db.upwork_oauth_mark_disconnected(message.from_user.id, revoked_at_iso=now_iso)
    await message.answer("Disconnected.")

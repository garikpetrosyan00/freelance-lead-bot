"""Stripe-powered PRO purchase command."""

from __future__ import annotations

import json
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.billing import create_checkout_session, get_stripe_checkout_config
from app.db import (
    create_upgrade_request_with_state,
    get_pending_upgrade_request_id,
    get_user_plan,
    get_user_settings,
    upsert_payment_from_checkout,
)
from app.gating import FREE_DAILY_CAP

router = Router()
logger = logging.getLogger(__name__)


def _settings_snapshot_json(user_id: int, username: str | None) -> str:
    plan = get_user_plan(user_id)
    min_level, user_cap = get_user_settings(user_id)
    return json.dumps(
        {
            "plan": plan,
            "min_level": min_level,
            "daily_cap": user_cap,
            "free_daily_cap": FREE_DAILY_CAP,
            "username": username,
        },
        ensure_ascii=True,
    )


@router.message(Command("buy_pro"))
async def handle_buy_pro(message: Message) -> None:
    user = message.from_user
    if user is None:
        await message.answer("Unable to identify user.")
        return

    cfg = get_stripe_checkout_config()
    if not cfg.enabled:
        reason = cfg.reason or "Stripe checkout is unavailable right now."
        await message.answer(
            f"/buy_pro is temporarily unavailable. {reason}\n"
            "You can still use /upgrade and /request_pro for manual activation."
        )
        return

    user_id = user.id
    username = f"@{user.username}" if user.username else None

    request_id = get_pending_upgrade_request_id(user_id)
    if request_id is None:
        snapshot = _settings_snapshot_json(user_id, username)
        request_id, _ = create_upgrade_request_with_state(
            user_id=user_id,
            username=username,
            settings_snapshot_json=snapshot,
            paid=0,
        )

    try:
        session = create_checkout_session(user_id=user_id, username=username, request_id=request_id)
        upsert_payment_from_checkout(session)
    except Exception:
        logger.exception("Failed to create checkout session for user_id=%s", user_id)
        await message.answer(
            "Could not create Stripe checkout right now. Please retry in a moment."
        )
        return

    checkout_url = str(session.get("url") or "").strip()
    if not checkout_url:
        await message.answer("Checkout session created but URL is missing. Please contact support.")
        return

    await message.answer(
        "Open this secure Stripe checkout URL to activate PRO:\n"
        f"{checkout_url}\n\n"
        "After payment, activation is automatic."
    )

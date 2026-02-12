"""User payment status and reconcile command."""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.analytics import log_event
from app.billing import (
    get_user_subscription_status,
    mask_id,
    reconcile_user_payment,
    stripe_reconcile_available,
)
from app.db import get_latest_payment_for_user, get_user_plan

router = Router()

_CREATED_STALE_MINUTES = 15


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.message(Command("payment_status"))
async def handle_payment_status(message: Message) -> None:
    user = message.from_user
    if user is None:
        await message.answer("Unable to identify user.")
        return

    user_id = user.id

    latest = get_latest_payment_for_user(user_id)
    if latest is None:
        plan = get_user_plan(user_id)
        configured, reason = stripe_reconcile_available()
        lines = [f"Plan: {plan}", "No payments found yet.", "Use /buy_pro to start checkout."]
        if not configured and reason:
            lines.append(reason)
        await message.answer("\n".join(lines))
        return

    log_event("reconcile_attempted", user_id=user_id, plan=get_user_plan(user_id), meta={"source": "/payment_status"})
    reconcile = reconcile_user_payment(user_id=user_id, force=False)
    if reconcile.changed:
        log_event(
            "reconcile_succeeded",
            user_id=user_id,
            plan="PRO",
            meta={"source": "/payment_status", "message": reconcile.message},
        )
    else:
        log_event(
            "reconcile_noop",
            user_id=user_id,
            plan=get_user_plan(user_id),
            meta={"source": "/payment_status", "message": reconcile.message},
        )
    latest = get_latest_payment_for_user(user_id)
    plan = get_user_plan(user_id)

    lines: list[str] = [f"Plan: {plan}"]

    payment_status = str(latest.get("status") if latest else "unknown")
    created_at = str(latest.get("created_at") if latest else "-")
    checkout_session_id = str(latest.get("checkout_session_id") if latest else "")
    subscription_status, subscription_id = get_user_subscription_status(user_id)

    lines.append(f"Last payment status: {payment_status}")
    lines.append(f"Created at: {created_at}")
    lines.append(f"Checkout session: {mask_id(checkout_session_id)}")
    lines.append(f"Subscription: {subscription_status or '-'} ({mask_id(subscription_id)})")

    if reconcile.changed:
        lines.append("Payment confirmed. PRO activated.")
    elif reconcile.attempted and reconcile.message:
        lines.append(reconcile.message)

    if payment_status == "created":
        created_dt = _parse_iso(created_at)
        if created_dt is not None:
            age_minutes = int((datetime.now(timezone.utc) - created_dt).total_seconds() / 60)
            if age_minutes >= _CREATED_STALE_MINUTES:
                lines.append(
                    "Checkout is still pending. If you completed payment, wait a moment and run /payment_status again."
                )
        lines.append("If checkout expired or failed, run /buy_pro to create a new session.")
    elif payment_status == "paid":
        lines.append("Payment is marked paid. PRO should be active.")
    elif payment_status == "failed":
        lines.append("Payment failed. Use /buy_pro to retry.")

    configured, reason = stripe_reconcile_available()
    if not configured and reason:
        lines.append(reason)

    await message.answer("\n".join(lines))

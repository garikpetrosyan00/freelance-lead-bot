"""Stripe payment reconciliation utilities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config import get_stripe_secret_key
from app.db import (
    activate_pro_for_user,
    attach_payment_to_upgrade_request,
    decide_upgrade_request,
    get_latest_payment_for_user,
    get_pending_upgrade_request_id,
    get_stripe_subscription_for_user,
    get_upgrade_request_by_id,
    get_user_plan,
    mark_payment_paid_by_session,
    upsert_stripe_subscription,
)

logger = logging.getLogger(__name__)

try:
    import stripe
except Exception:  # pragma: no cover - optional dependency guard
    stripe = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ReconcileResult:
    attempted: bool
    changed: bool
    message: str


def mask_id(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "-"
    if len(raw) <= 8:
        return f"{raw[:2]}***"
    return f"{raw[:8]}***{raw[-4:]}"


def stripe_reconcile_available() -> tuple[bool, str | None]:
    if stripe is None:
        return False, "Payments are not configured right now."
    secret_key = get_stripe_secret_key()
    if not secret_key:
        return False, "Payments are not configured right now."
    return True, None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            mapped = to_dict()
            if isinstance(mapped, dict):
                return mapped
        except Exception:
            return {}
    return {}


def _iso_from_unix(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _maybe_auto_approve(user_id: int, request_id: int | None, note: str) -> None:
    target_request_id = request_id or get_pending_upgrade_request_id(user_id)
    if target_request_id is None:
        return

    request = get_upgrade_request_by_id(target_request_id)
    if request is None:
        return
    if int(request["user_id"]) != int(user_id):
        logger.warning(
            "Stripe reconcile skipped auto-approve request_id=%s due to user mismatch",
            target_request_id,
        )
        return
    if str(request["status"]) != "pending":
        return

    attach_payment_to_upgrade_request(target_request_id, paid=1)
    decide_upgrade_request(
        request_id=target_request_id,
        new_status="approved",
        admin_id=0,
        admin_note=note,
        decided_at_iso=_utc_now_iso(),
    )


def _extract_session_subscription(session: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    subscription_value = session.get("subscription")
    subscription_id = None
    sub_status = None
    customer_id = str(session.get("customer") or "") or None
    current_period_end = None

    if isinstance(subscription_value, str):
        subscription_id = subscription_value or None
    else:
        sub_obj = _as_dict(subscription_value)
        subscription_id = str(sub_obj.get("id") or "") or None
        sub_status = str(sub_obj.get("status") or "") or None
        if sub_obj.get("customer") is not None:
            customer_id = str(sub_obj.get("customer") or "") or customer_id
        current_period_end = _iso_from_unix(sub_obj.get("current_period_end"))

    return subscription_id, customer_id, sub_status, current_period_end


def _is_session_paid(session: dict[str, Any], sub_status: str | None) -> bool:
    payment_status = str(session.get("payment_status") or "").lower()
    status = str(session.get("status") or "").lower()
    if payment_status == "paid":
        return True
    if status == "complete":
        if sub_status:
            return sub_status.lower() in {"active", "trialing"}
        return True
    return False


def reconcile_user_payment(user_id: int, force: bool = False) -> ReconcileResult:
    latest = get_latest_payment_for_user(user_id)
    plan = get_user_plan(user_id)

    if latest is None:
        return ReconcileResult(attempted=False, changed=False, message="No payment record found.")

    checkout_session_id = str(latest.get("checkout_session_id") or "").strip() or None
    subscription_id = str(latest.get("subscription_id") or "").strip() or None
    should_attempt = force or latest.get("status") == "created" or (
        plan == "FREE" and bool(checkout_session_id or subscription_id)
    )
    if not should_attempt:
        return ReconcileResult(attempted=False, changed=False, message="No reconcile needed.")

    configured, reason = stripe_reconcile_available()
    if not configured:
        return ReconcileResult(attempted=False, changed=False, message=reason or "Payments are not configured.")

    secret_key = get_stripe_secret_key()
    if stripe is None or not secret_key:
        return ReconcileResult(attempted=False, changed=False, message="Payments are not configured right now.")

    stripe.api_key = secret_key

    try:
        if checkout_session_id:
            session_obj = stripe.checkout.Session.retrieve(
                checkout_session_id,
                expand=["subscription", "customer", "payment_intent"],
            )
            session = _as_dict(session_obj)
            payment_intent_id = str(session.get("payment_intent") or "") or None
            subscription_id_resolved, customer_id, sub_status, current_period_end = _extract_session_subscription(session)
            if subscription_id_resolved and not sub_status:
                sub_obj = stripe.Subscription.retrieve(subscription_id_resolved)
                sub = _as_dict(sub_obj)
                sub_status = str(sub.get("status") or "") or None
                current_period_end = _iso_from_unix(sub.get("current_period_end")) or current_period_end
            if _is_session_paid(session, sub_status):
                mark_payment_paid_by_session(
                    checkout_session_id,
                    payment_intent_id=payment_intent_id,
                    subscription_id=subscription_id_resolved,
                    customer_id=customer_id,
                    amount_total=int(session.get("amount_total")) if session.get("amount_total") is not None else None,
                    currency=str(session.get("currency") or "") or None,
                )
                if subscription_id_resolved:
                    upsert_stripe_subscription(
                        user_id=user_id,
                        subscription_id=subscription_id_resolved,
                        customer_id=customer_id,
                        status=sub_status or "active",
                        current_period_end=current_period_end,
                    )
                activate_pro_for_user(user_id, reason="stripe_reconcile_session_paid", activated_at_iso=_utc_now_iso())
                request_id = latest.get("request_id")
                _maybe_auto_approve(user_id, request_id, "auto reconcile via /payment_status")
                return ReconcileResult(
                    attempted=True,
                    changed=True,
                    message="Payment confirmed via Stripe Checkout.",
                )
            return ReconcileResult(
                attempted=True,
                changed=False,
                message=(
                    f"Checkout not paid yet (status={session.get('status')}, "
                    f"payment_status={session.get('payment_status')})."
                ),
            )

        if subscription_id:
            sub_obj = stripe.Subscription.retrieve(subscription_id)
            sub = _as_dict(sub_obj)
            status = str(sub.get("status") or "").lower() or "unknown"
            customer_id = str(sub.get("customer") or "") or None
            upsert_stripe_subscription(
                user_id=user_id,
                subscription_id=subscription_id,
                customer_id=customer_id,
                status=status,
                current_period_end=_iso_from_unix(sub.get("current_period_end")),
            )
            if status in {"active", "trialing"}:
                activate_pro_for_user(user_id, reason="stripe_reconcile_subscription_active", activated_at_iso=_utc_now_iso())
                request_id = latest.get("request_id")
                _maybe_auto_approve(user_id, request_id, "auto reconcile via /payment_status")
                return ReconcileResult(
                    attempted=True,
                    changed=True,
                    message=f"Subscription is {status}; PRO activated.",
                )
            return ReconcileResult(
                attempted=True,
                changed=False,
                message=f"Subscription status is {status}.",
            )
    except Exception:
        logger.exception("Stripe reconcile failed for user_id=%s", user_id)
        return ReconcileResult(
            attempted=True,
            changed=False,
            message="Could not verify payment with Stripe right now. Please try again shortly.",
        )

    return ReconcileResult(attempted=False, changed=False, message="No Stripe identifiers available for reconcile.")


def get_user_subscription_status(user_id: int) -> tuple[str | None, str | None]:
    sub = get_stripe_subscription_for_user(user_id)
    if not sub:
        return None, None
    return str(sub.get("status") or "") or None, str(sub.get("subscription_id") or "") or None

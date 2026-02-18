"""Stripe checkout helpers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from app.config import (
    get_public_base_url,
    get_stripe_cancel_path,
    get_stripe_currency,
    get_stripe_mode,
    get_stripe_price_id,
    get_stripe_secret_key,
    get_stripe_success_path,
)

logger = logging.getLogger(__name__)

try:
    import stripe
except Exception:  # pragma: no cover - optional dependency guard
    stripe = None  # type: ignore[assignment]


@dataclass(frozen=True)
class StripeCheckoutConfig:
    enabled: bool
    reason: str | None
    mode: str
    price_id: str | None
    currency: str
    public_base_url: str
    success_path: str
    cancel_path: str


def get_stripe_checkout_config() -> StripeCheckoutConfig:
    secret_key = get_stripe_secret_key()
    price_id = get_stripe_price_id()
    public_base_url = get_public_base_url()
    try:
        mode = get_stripe_mode()
    except RuntimeError as exc:
        return StripeCheckoutConfig(
            enabled=False,
            reason=str(exc),
            mode="subscription",
            price_id=price_id,
            currency=get_stripe_currency(),
            public_base_url=public_base_url,
            success_path=get_stripe_success_path(),
            cancel_path=get_stripe_cancel_path(),
        )
    currency = get_stripe_currency()
    success_path = get_stripe_success_path()
    cancel_path = get_stripe_cancel_path()

    if stripe is None:
        return StripeCheckoutConfig(
            enabled=False,
            reason="Stripe dependency is not installed.",
            mode=mode,
            price_id=price_id,
            currency=currency,
            public_base_url=public_base_url,
            success_path=success_path,
            cancel_path=cancel_path,
        )
    if not secret_key:
        return StripeCheckoutConfig(
            enabled=False,
            reason="STRIPE_SECRET is not configured.",
            mode=mode,
            price_id=price_id,
            currency=currency,
            public_base_url=public_base_url,
            success_path=success_path,
            cancel_path=cancel_path,
        )
    if not price_id:
        return StripeCheckoutConfig(
            enabled=False,
            reason="STRIPE_PRICE_ID is not configured.",
            mode=mode,
            price_id=price_id,
            currency=currency,
            public_base_url=public_base_url,
            success_path=success_path,
            cancel_path=cancel_path,
        )

    return StripeCheckoutConfig(
        enabled=True,
        reason=None,
        mode=mode,
        price_id=price_id,
        currency=currency,
        public_base_url=public_base_url,
        success_path=success_path,
        cancel_path=cancel_path,
    )


def create_checkout_session(
    *,
    user_id: int,
    username: str | None,
    request_id: int | None,
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> dict[str, Any]:
    config = get_stripe_checkout_config()
    if not config.enabled:
        raise RuntimeError(config.reason or "Stripe checkout is not configured")

    secret_key = get_stripe_secret_key()
    if stripe is None or not secret_key:
        raise RuntimeError("Stripe checkout is not configured")

    stripe.api_key = secret_key

    metadata: dict[str, str] = {
        "user_id": str(user_id),
        "telegram_user_id": str(user_id),
        "username": username or "",
    }
    if request_id is not None:
        metadata["request_id"] = str(request_id)

    bot_username = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    return_to_bot_url = (
        f"https://t.me/{bot_username}?start=pro_return"
        if bot_username
        else None
    )

    resolved_success_url = success_url
    resolved_cancel_url = cancel_url
    if return_to_bot_url:
        resolved_success_url = return_to_bot_url
        resolved_cancel_url = return_to_bot_url
    if not resolved_success_url:
        resolved_success_url = f"{config.public_base_url}{config.success_path}?session_id={{CHECKOUT_SESSION_ID}}"
    if not resolved_cancel_url:
        resolved_cancel_url = f"{config.public_base_url}{config.cancel_path}"

    kwargs: dict[str, Any] = {
        "mode": config.mode,
        "line_items": [{"price": config.price_id, "quantity": 1}],
        "success_url": resolved_success_url,
        "cancel_url": resolved_cancel_url,
        "metadata": metadata,
        "client_reference_id": str(user_id),
    }
    if config.mode == "subscription":
        kwargs["subscription_data"] = {"metadata": metadata}

    session = stripe.checkout.Session.create(**kwargs)
    result = session if isinstance(session, dict) else session.to_dict()
    if return_to_bot_url:
        result["return_to_bot_url"] = return_to_bot_url
    return result

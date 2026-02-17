"""Stripe webhook server for payment-driven PRO activation."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot

from app.analytics import log_event
from app.config import (
    get_lemon_webhook_secret,
    get_stripe_secret_key,
    get_stripe_webhook_secret,
    get_webhook_host,
    get_webhook_port,
)
from app.db import (
    activate_pro_for_user,
    attach_payment_to_upgrade_request,
    decide_upgrade_request,
    downgrade_user_to_free,
    get_pending_upgrade_request_id,
    get_upgrade_request_by_id,
    get_payment_user_id_by_subscription_id,
    get_subscription_user_id,
    mark_event_processed,
    mark_payment_paid_by_session,
    mark_payment_paid_by_subscription_id,
    unmark_event_processed,
    update_payment_status_by_subscription_id,
    upsert_payment_from_checkout,
    upsert_stripe_subscription,
    record_error,
)
from app.ops.logging_utils import log_kv, mask_stripe_id, mask_user_id, safe_exc

logger = logging.getLogger(__name__)

try:
    import stripe
except Exception:  # pragma: no cover - optional dependency guard
    stripe = None  # type: ignore[assignment]

try:
    import uvicorn
    from fastapi import FastAPI, Header, Request
    from fastapi.responses import JSONResponse
except Exception:  # pragma: no cover - optional dependency guard
    uvicorn = None  # type: ignore[assignment]
    FastAPI = None  # type: ignore[assignment]
    Header = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]
    JSONResponse = None  # type: ignore[assignment]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_from_unix(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def is_stripe_webhook_enabled() -> tuple[bool, str | None]:
    if stripe is None:
        return False, "stripe dependency is missing"
    if FastAPI is None or uvicorn is None:
        return False, "fastapi/uvicorn dependencies are missing"
    if not get_stripe_secret_key():
        return False, "STRIPE_SECRET_KEY is not configured"
    if not get_stripe_webhook_secret():
        return False, "STRIPE_WEBHOOK_SECRET is not configured"
    return True, None


def _extract_user_id_from_metadata(metadata: dict[str, Any] | None) -> int | None:
    if not metadata:
        return None
    raw = metadata.get("user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _extract_request_id_from_metadata(metadata: dict[str, Any] | None) -> int | None:
    if not metadata:
        return None
    raw = metadata.get("request_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _notify_user(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id=user_id, text=text)
    except Exception as exc:
        record_error("notify", exc, context={"user_id": user_id, "action": "stripe_event_notify"})
        log_kv(
            logger,
            logging.WARNING,
            "Failed to notify user for Stripe event",
            user=mask_user_id(user_id),
            error=safe_exc(exc),
        )


def _auto_approve_upgrade_request(user_id: int, request_id: int | None, decided_at: str) -> None:
    target_request_id = request_id or get_pending_upgrade_request_id(user_id)
    if target_request_id is None:
        return

    request = get_upgrade_request_by_id(target_request_id)
    if request is None:
        logger.warning(
            "Stripe auto-approve skipped: request_id=%s not found",
            target_request_id,
        )
        return
    if int(request["user_id"]) != int(user_id):
        log_kv(
            logger,
            logging.WARNING,
            "Stripe auto-approve skipped due to user mismatch",
            request_id=target_request_id,
            request_user=mask_user_id(request["user_id"]),
            event_user=mask_user_id(user_id),
        )
        return
    if str(request["status"]) != "pending":
        logger.warning(
            "Stripe auto-approve skipped: request_id=%s status=%s",
            target_request_id,
            request["status"],
        )
        return

    attach_payment_to_upgrade_request(target_request_id, paid=1)
    decided = decide_upgrade_request(
        request_id=target_request_id,
        new_status="approved",
        admin_id=0,
        admin_note="auto via stripe",
        decided_at_iso=decided_at,
    )
    if decided:
        log_event(
            "upgrade_approved",
            user_id=user_id,
            plan="PRO",
            meta={"request_id": target_request_id, "source": "stripe_webhook_auto"},
            ts=decided_at,
        )


async def _handle_checkout_session_completed(bot: Bot, event: dict[str, Any]) -> None:
    session = event.get("data", {}).get("object", {})
    metadata = session.get("metadata") or {}
    user_id = _extract_user_id_from_metadata(metadata)
    if user_id is None:
        logger.warning("Stripe checkout.session.completed missing metadata.user_id")
        return

    request_id = _extract_request_id_from_metadata(metadata)
    session_id = str(session.get("id") or "").strip()
    if not session_id:
        logger.warning("Stripe checkout.session.completed missing session id")
        return

    upsert_payment_from_checkout(session)
    mark_payment_paid_by_session(
        session_id,
        payment_intent_id=str(session.get("payment_intent") or "") or None,
        subscription_id=str(session.get("subscription") or "") or None,
        customer_id=str(session.get("customer") or "") or None,
        amount_total=int(session.get("amount_total")) if session.get("amount_total") is not None else None,
        currency=str(session.get("currency") or "") or None,
    )
    log_event(
        "checkout_completed",
        user_id=user_id,
        plan="PRO",
        meta={
            "source": "webhook:checkout.session.completed",
            "checkout_session_id": session_id,
            "subscription_id": str(session.get("subscription") or "") or None,
        },
    )
    log_event(
        "payment_confirmed",
        user_id=user_id,
        plan="PRO",
        meta={
            "source": "webhook:checkout.session.completed",
            "checkout_session_id": session_id,
            "subscription_id": str(session.get("subscription") or "") or None,
        },
    )

    subscription_id = str(session.get("subscription") or "").strip()
    customer_id = str(session.get("customer") or "").strip() or None
    if subscription_id:
        upsert_stripe_subscription(
            user_id=user_id,
            subscription_id=subscription_id,
            customer_id=customer_id,
            status="active",
            current_period_end=None,
        )

    decided_at = _utc_now_iso()
    activate_pro_for_user(user_id=user_id, reason="stripe_checkout_completed", activated_at_iso=decided_at)
    _auto_approve_upgrade_request(user_id=user_id, request_id=request_id, decided_at=decided_at)

    await _notify_user(
        bot,
        user_id,
        "Payment received. PRO activated.\nUse /settings to adjust your PRO limits.",
    )


async def _handle_invoice_paid(bot: Bot, event: dict[str, Any]) -> None:
    invoice = event.get("data", {}).get("object", {})
    subscription_id = str(invoice.get("subscription") or "").strip()
    if not subscription_id:
        return

    amount_paid = invoice.get("amount_paid")
    mark_payment_paid_by_subscription_id(
        subscription_id,
        amount_total=int(amount_paid) if amount_paid is not None else None,
        currency=str(invoice.get("currency") or "") or None,
        status="paid",
    )

    user_id = get_subscription_user_id(subscription_id) or get_payment_user_id_by_subscription_id(subscription_id)
    if user_id is None:
        return
    log_event(
        "payment_confirmed",
        user_id=user_id,
        plan="PRO",
        meta={"source": "webhook:invoice.paid", "subscription_id": subscription_id},
    )

    status = str(invoice.get("status") or "paid")
    period_end = _iso_from_unix(invoice.get("period_end"))
    upsert_stripe_subscription(
        user_id=user_id,
        subscription_id=subscription_id,
        customer_id=str(invoice.get("customer") or "") or None,
        status=status,
        current_period_end=period_end,
    )

    activate_pro_for_user(user_id=user_id, reason="stripe_invoice_paid", activated_at_iso=_utc_now_iso())
    await _notify_user(bot, user_id, "Subscription payment received. PRO remains active.")


async def _handle_subscription_updated(event: dict[str, Any]) -> None:
    sub = event.get("data", {}).get("object", {})
    subscription_id = str(sub.get("id") or "").strip()
    if not subscription_id:
        return

    metadata = sub.get("metadata") or {}
    user_id = _extract_user_id_from_metadata(metadata)
    if user_id is None:
        user_id = get_subscription_user_id(subscription_id) or get_payment_user_id_by_subscription_id(subscription_id)
    if user_id is None:
        return

    status = str(sub.get("status") or "incomplete")
    current_period_end = _iso_from_unix(sub.get("current_period_end"))
    upsert_stripe_subscription(
        user_id=user_id,
        subscription_id=subscription_id,
        customer_id=str(sub.get("customer") or "") or None,
        status=status,
        current_period_end=current_period_end,
    )

    if status in {"active", "trialing"}:
        activate_pro_for_user(user_id=user_id, reason="stripe_subscription_updated", activated_at_iso=_utc_now_iso())


async def _handle_subscription_deleted(bot: Bot, event: dict[str, Any]) -> None:
    sub = event.get("data", {}).get("object", {})
    subscription_id = str(sub.get("id") or "").strip()
    if not subscription_id:
        return

    user_id = get_subscription_user_id(subscription_id) or get_payment_user_id_by_subscription_id(subscription_id)
    if user_id is None:
        return

    status = str(sub.get("status") or "canceled")
    current_period_end = _iso_from_unix(sub.get("current_period_end"))
    upsert_stripe_subscription(
        user_id=user_id,
        subscription_id=subscription_id,
        customer_id=str(sub.get("customer") or "") or None,
        status=status,
        current_period_end=current_period_end,
    )
    update_payment_status_by_subscription_id(subscription_id, status="canceled")
    downgrade_user_to_free(user_id=user_id, reason="stripe_subscription_deleted", updated_at_iso=_utc_now_iso())
    await _notify_user(bot, user_id, "Your Stripe subscription ended. Plan changed to FREE.")


async def _handle_invoice_payment_failed(bot: Bot, event: dict[str, Any]) -> None:
    invoice = event.get("data", {}).get("object", {})
    subscription_id = str(invoice.get("subscription") or "").strip()
    if not subscription_id:
        return

    user_id = get_subscription_user_id(subscription_id) or get_payment_user_id_by_subscription_id(subscription_id)
    if user_id is None:
        return

    update_payment_status_by_subscription_id(subscription_id, status="failed")
    upsert_stripe_subscription(
        user_id=user_id,
        subscription_id=subscription_id,
        customer_id=str(invoice.get("customer") or "") or None,
        status="past_due",
        current_period_end=_iso_from_unix(invoice.get("period_end")),
    )
    downgrade_user_to_free(user_id=user_id, reason="stripe_invoice_payment_failed", updated_at_iso=_utc_now_iso())
    await _notify_user(bot, user_id, "Stripe payment failed. Plan changed to FREE until payment is restored.")


async def _dispatch_event(bot: Bot, event: dict[str, Any]) -> None:
    event_type = str(event.get("type") or "")
    if event_type == "checkout.session.completed":
        await _handle_checkout_session_completed(bot, event)
        return
    if event_type == "invoice.paid":
        await _handle_invoice_paid(bot, event)
        return
    if event_type == "customer.subscription.updated":
        await _handle_subscription_updated(event)
        return
    if event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(bot, event)
        return
    if event_type == "invoice.payment_failed":
        await _handle_invoice_payment_failed(bot, event)
        return

    logger.info("Unhandled Stripe event type=%s", event_type)


def create_webhook_app(bot: Bot):
    if FastAPI is None:
        raise RuntimeError("Webhook dependencies are unavailable")

    app = FastAPI()

    stripe_enabled, _ = is_stripe_webhook_enabled()
    if stripe_enabled:
        webhook_secret = get_stripe_webhook_secret()
        secret_key = get_stripe_secret_key()
        if stripe is None:
            raise RuntimeError("stripe dependency is missing")
        if stripe is not None and secret_key:
            stripe.api_key = secret_key

        @app.get("/stripe/success")
        async def stripe_success(session_id: str | None = None):  # type: ignore[no-redef]
            return {"ok": True, "message": "Payment completed. You can return to Telegram.", "session_id": session_id}

        @app.get("/stripe/cancel")
        async def stripe_cancel():  # type: ignore[no-redef]
            return {"ok": True, "message": "Payment canceled. You can retry /buy_pro in Telegram."}

        @app.post("/webhooks/stripe")
        async def stripe_webhook(  # type: ignore[no-redef]
            request: Request,
            stripe_signature: str | None = Header(default=None, alias="stripe-signature"),
        ):
            payload = await request.body()
            if not stripe_signature:
                return JSONResponse(status_code=400, content={"error": "missing stripe-signature"})

            try:
                event = stripe.Webhook.construct_event(payload, stripe_signature, webhook_secret)
            except Exception as exc:
                record_error("webhook", exc, context={"action": "stripe_signature_verify"})
                log_kv(logger, logging.WARNING, "Invalid Stripe webhook signature", error=safe_exc(exc))
                return JSONResponse(status_code=400, content={"error": "invalid signature"})

            event_id = str(event.get("id") or "").strip()
            if not event_id:
                logger.warning("Stripe webhook without event id")
                return {"ok": True}

            first_seen = mark_event_processed("stripe", event_id)
            if not first_seen:
                return {"ok": True, "duplicate": True}

            try:
                await _dispatch_event(bot, event)
            except Exception as exc:
                unmark_event_processed("stripe", event_id)
                record_error(
                    "webhook",
                    exc,
                    context={
                        "event_id": event_id,
                        "event_type": str(event.get("type") or ""),
                        "action": "dispatch_event",
                    },
                )
                log_kv(
                    logger,
                    logging.ERROR,
                    "Failed processing Stripe event",
                    event_id=mask_stripe_id(event_id),
                    event_type=str(event.get("type") or ""),
                    error=safe_exc(exc),
                )
                return JSONResponse(status_code=500, content={"error": "processing failed"})

            return {"ok": True}

    @app.post("/webhooks/lemon")
    async def lemon_webhook(  # type: ignore[no-redef]
        request: Request,
        signature: str | None = Header(default=None, alias="X-Signature"),
        event_name: str | None = Header(default=None, alias="X-Event-Name"),
    ):
        payload = await request.body()
        if not signature or not event_name:
            return JSONResponse(status_code=400, content={"error": "missing required headers"})

        secret = get_lemon_webhook_secret()
        if not secret:
            return JSONResponse(status_code=500, content={"error": "LEMONSQUEEZY_WEBHOOK_SECRET not configured"})
        expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.strip(), expected):
            return JSONResponse(status_code=401, content={"error": "invalid signature"})

        event_key = ""
        try:
            payload_obj = json.loads(payload.decode("utf-8"))
            if not isinstance(payload_obj, dict):
                payload_obj = {}

            data_obj = payload_obj.get("data") or {}
            if not isinstance(data_obj, dict):
                data_obj = {}
            subscription_id = str(data_obj.get("id") or "").strip()
            if not subscription_id:
                return JSONResponse(status_code=400, content={"error": "missing subscription id"})

            attributes = data_obj.get("attributes") or {}
            if not isinstance(attributes, dict):
                attributes = {}
            updated_at = attributes.get("updated_at") or attributes.get("created_at")

            meta_obj = payload_obj.get("meta") or {}
            if not isinstance(meta_obj, dict):
                meta_obj = {}
            custom_data = meta_obj.get("custom_data") or {}
            if not isinstance(custom_data, dict):
                custom_data = {}
            telegram_user_id = custom_data.get("telegram_user_id")

            event_key = f"lemon:{event_name}:{subscription_id}:{updated_at or 'na'}"
            first_seen = mark_event_processed("lemon", event_key)
            if not first_seen:
                return {"ok": True, "duplicate": True}

            if event_name == "subscription_created":
                try:
                    user_id = int(telegram_user_id)
                except (TypeError, ValueError):
                    unmark_event_processed("lemon", event_key)
                    return JSONResponse(status_code=400, content={"error": "missing telegram_user_id"})

                activate_pro_for_user(
                    user_id=user_id,
                    reason="lemon_subscription_created",
                    activated_at_iso=_utc_now_iso(),
                )
                return {"ok": True}

            if event_name == "subscription_cancelled":
                try:
                    user_id = int(telegram_user_id)
                except (TypeError, ValueError):
                    unmark_event_processed("lemon", event_key)
                    return JSONResponse(status_code=400, content={"error": "missing telegram_user_id"})

                log_event(
                    "subscription_cancelled",
                    user_id=user_id,
                    plan="PRO",
                    meta={"provider": "lemon", "subscription_id": subscription_id},
                    ts=_utc_now_iso(),
                )
                return {"ok": True}

            if event_name == "subscription_expired":
                try:
                    user_id = int(telegram_user_id)
                except (TypeError, ValueError):
                    unmark_event_processed("lemon", event_key)
                    return JSONResponse(status_code=400, content={"error": "missing telegram_user_id"})

                downgrade_user_to_free(
                    user_id=user_id,
                    reason="lemon_subscription_expired",
                    updated_at_iso=_utc_now_iso(),
                )
                log_event(
                    "pro_downgraded",
                    user_id=user_id,
                    plan="FREE",
                    meta={"reason": "lemon_expired", "subscription_id": subscription_id},
                    ts=_utc_now_iso(),
                )
                return {"ok": True}

            return {"ok": True, "ignored": True}
        except Exception as exc:
            if event_key:
                unmark_event_processed("lemon", event_key)
            record_error(
                "webhook",
                exc,
                context={
                    "provider": "lemon",
                    "event_key": event_key,
                    "event_name": event_name or "",
                    "action": "lemon_dispatch_event",
                },
            )
            log_kv(
                logger,
                logging.ERROR,
                "Failed processing Lemon event",
                event_key=event_key,
                event_name=event_name or "",
                error=safe_exc(exc),
            )
            return JSONResponse(status_code=500, content={"error": "processing failed"})

    return app


async def run_webhook_server(bot: Bot, ready: asyncio.Event | None = None) -> None:
    stripe_enabled, stripe_reason = is_stripe_webhook_enabled()
    lemon_enabled = bool(get_lemon_webhook_secret())

    if FastAPI is None or uvicorn is None:
        logger.info("Webhook server disabled: fastapi/uvicorn dependencies are missing")
        return

    logger.info(
        "Webhook routes: stripe=%s lemon=%s",
        "enabled" if stripe_enabled else f"disabled ({stripe_reason or 'not configured'})",
        "enabled" if lemon_enabled else "disabled (LEMONSQUEEZY_WEBHOOK_SECRET not configured)",
    )
    if not stripe_enabled and not lemon_enabled:
        logger.info("Webhook server disabled: no webhook providers configured")
        return

    app = create_webhook_app(bot)
    host = get_webhook_host()
    port = get_webhook_port()
    config = uvicorn.Config(app=app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    logger.info("Starting webhook server on %s:%s", host, port)
    server_task = asyncio.create_task(server.serve())
    startup_ready = False
    try:
        while True:
            if getattr(server, "started", False):
                if ready is not None and not ready.is_set():
                    ready.set()
                startup_ready = True
                break
            if server_task.done():
                # Startup failed before server reported ready.
                await server_task
                raise RuntimeError("Webhook server exited before reporting ready")
            await asyncio.sleep(0.05)

        await server_task
    except asyncio.CancelledError:
        server.should_exit = True
        raise
    finally:
        if not startup_ready:
            server.should_exit = True
        if not server_task.done():
            server_task.cancel()
        with suppress(BaseException):
            await server_task


async def run_stripe_webhook_server(bot: Bot, ready: asyncio.Event | None = None) -> None:
    await run_webhook_server(bot, ready=ready)

"""Application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, Dispatcher

from app import db
from app.analytics import log_event
from app.db import expire_overdue_pro_users, record_error
from app.config import (
    demo_mode_enabled,
    enable_fake_ingestion,
    enable_telegram_ingestion,
    get_upwork_poll_mode,
    load_config,
)
from app.handlers import (
    analytics_admin_router,
    buy_pro_router,
    monitoring_admin_router,
    my_id_router,
    payment_admin_router,
    payment_status_router,
    plan_router,
    settings_router,
    skills_router,
    skills_picker_router,
    start_router,
    support_router,
    subscription_router,
    test_lead_router,
    upwork_alerts_router,
    upwork_api_router,
    upwork_oauth_router,
    upwork_profiles_router,
    upwork_status_router,
    ui_flow_router,
    ui_settings_router,
    upgrade_request_router,
)
from app.ingestion.telegram_listener import run_telegram_listener
from app.jobs.poller import run_upwork_rss_poller
from app.monitoring import run_monitor_loop
from app.middlewares.incoming_debug import IncomingDebugMiddleware
from app.middlewares.rate_limit import RateLimitMiddleware
from app.pollers.upwork_api_poller import run_upwork_api_poller
from app.pipeline import run_fake_ingestion
from app.webhooks import is_stripe_webhook_enabled, run_stripe_webhook_server

BASE_CRITICAL_TABLES = (
    "upgrade_requests",
    "analytics_events",
    "monitor_state",
    "monitor_alerts",
    "error_events",
    "upwork_rss_feeds",
    "upwork_jobs_seen",
    "upwork_daily_counters",
    "upwork_user_prefs",
)
STRIPE_CRITICAL_TABLES = ("payments", "processed_events")
PRO_EXPIRY_CHECK_INTERVAL_SECONDS = 5 * 60
_UPWORK_POLL_TASK: asyncio.Task | None = None
OAUTH_STATE_CLEANUP_INTERVAL_SECONDS = 3600


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _log_task_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        db.record_error("monitoring", exc, context={"phase": "background_task"})
        logging.getLogger(__name__).exception(
            "Background task crashed", exc_info=exc
        )


async def run_pro_expiry_loop(bot: Bot, interval_seconds: int = PRO_EXPIRY_CHECK_INTERVAL_SECONDS) -> None:
    safe_interval = max(60, int(interval_seconds))
    logger = logging.getLogger(__name__)
    logger.info("PRO expiry loop started (interval=%ss)", safe_interval)
    while True:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            downgraded_user_ids = expire_overdue_pro_users(now_iso)
            for user_id in downgraded_user_ids:
                log_event(
                    "pro_expired",
                    user_id=user_id,
                    plan="FREE",
                    meta={"source": "expiry_loop"},
                    ts=now_iso,
                )
                try:
                    await bot.send_message(chat_id=user_id, text="Your PRO subscription has expired.")
                except Exception as exc:
                    record_error(
                        "billing",
                        exc,
                        context={"user_id": user_id, "action": "notify_pro_expired"},
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record_error("billing", exc, context={"action": "expire_overdue_pro_users"})
            logger.warning("PRO expiry loop iteration failed", exc_info=True)
        await asyncio.sleep(safe_interval)


async def run_oauth_state_cleanup_loop(interval_seconds: int = OAUTH_STATE_CLEANUP_INTERVAL_SECONDS) -> None:
    safe_interval = max(300, int(interval_seconds))
    logger = logging.getLogger(__name__)
    logger.info("OAuth state cleanup loop started (interval=%ss)", safe_interval)
    while True:
        try:
            removed = db.upwork_oauth_state_cleanup(datetime.now(timezone.utc).isoformat())
            logger.info("OAuth state cleanup done removed=%s", removed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            db.record_error("upwork_oauth", exc, context={"action": "state_cleanup"})
            logger.warning("OAuth state cleanup failed", exc_info=True)
        await asyncio.sleep(safe_interval)


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    demo_mode = demo_mode_enabled()

    token = load_config()
    stripe_webhook_enabled, stripe_webhook_reason = is_stripe_webhook_enabled()
    required_tables = list(BASE_CRITICAL_TABLES)
    if stripe_webhook_enabled:
        required_tables.extend(STRIPE_CRITICAL_TABLES)
    try:
        db.run_startup_sanity_checks(required_tables)
    except Exception as exc:
        db.record_error("db", exc, context={"phase": "startup_sanity"})
        logger.critical("DB startup sanity checks failed; refusing to start", exc_info=True)
        raise SystemExit(1)

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.message.middleware(IncomingDebugMiddleware())
    dp.message.middleware(RateLimitMiddleware())

    async def _handle_dispatch_error(event: Any) -> bool:
        exc = getattr(event, "exception", None)
        db.record_error("bot", exc if isinstance(exc, Exception) else Exception("unknown bot error"))
        logger.exception("Unhandled aiogram update error", exc_info=exc)

        update = getattr(event, "update", None)
        message = getattr(update, "message", None)
        if message is not None:
            with suppress(Exception):
                await message.answer("Temporary error. Please try again.")
        return True

    dp.errors.register(_handle_dispatch_error)

    routers = [
        ("start", start_router),
        ("ui_flow", ui_flow_router),
        ("support", support_router),
        ("ui_settings", ui_settings_router),
        ("skills", skills_router),
        ("skills_picker", skills_picker_router),
        ("test_lead", test_lead_router),
        ("subscription", subscription_router),
        ("plan", plan_router),
        ("settings", settings_router),
        ("my_id", my_id_router),
        ("buy_pro", buy_pro_router),
        ("payment_status", payment_status_router),
        ("payment_admin", payment_admin_router),
        ("analytics_admin", analytics_admin_router),
        ("monitoring_admin", monitoring_admin_router),
        ("upgrade_request", upgrade_request_router),
        ("upwork_alerts", upwork_alerts_router),
        ("upwork_oauth", upwork_oauth_router),
        ("upwork_profiles", upwork_profiles_router),
        ("upwork_status", upwork_status_router),
        ("upwork_api", upwork_api_router),
    ]
    for _, router in routers:
        dp.include_router(router)
    logger.info("routers included: %s", ", ".join(name for name, _ in routers))

    tasks: list[asyncio.Task] = []
    logger.info("bot started")

    if demo_mode:
        logger.warning("DEMO_MODE enabled: external integrations disabled")

    if enable_fake_ingestion():
        tasks.append(asyncio.create_task(run_fake_ingestion(bot)))
        logger.info("Fake ingestion enabled")

    if not demo_mode and enable_telegram_ingestion():
        telethon_task = asyncio.create_task(run_telegram_listener(bot))
        telethon_task.add_done_callback(_log_task_failure)
        tasks.append(telethon_task)
        logger.info("Telegram ingestion enabled")

    if not demo_mode:
        webhook_ready: asyncio.Event | None = asyncio.Event() if stripe_webhook_enabled else None
        if stripe_webhook_enabled:
            logger.info("webhook setup: enabled")
        else:
            logger.info("webhook setup: disabled (%s)", stripe_webhook_reason or "not configured")
        webhook_task = asyncio.create_task(run_stripe_webhook_server(bot, ready=webhook_ready))
        webhook_task.add_done_callback(_log_task_failure)
        tasks.append(webhook_task)
        if stripe_webhook_enabled:
            ready_wait_task = asyncio.create_task(webhook_ready.wait())
            done, _ = await asyncio.wait(
                {ready_wait_task, webhook_task},
                timeout=5.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            started = ready_wait_task in done and webhook_ready.is_set()
            if not started:
                if webhook_task in done:
                    try:
                        webhook_task.result()
                    except BaseException as exc:
                        logger.exception("Stripe webhook failed to start - aborting", exc_info=exc)
                else:
                    logger.error("Stripe webhook failed to start - aborting")
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with suppress(BaseException):
                        await task
                ready_wait_task.cancel()
                with suppress(asyncio.CancelledError):
                    await ready_wait_task
                await bot.session.close()
                raise SystemExit(1)
            ready_wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await ready_wait_task

    monitor_task = asyncio.create_task(run_monitor_loop(bot))
    monitor_task.add_done_callback(_log_task_failure)
    tasks.append(monitor_task)

    expiry_task = asyncio.create_task(run_pro_expiry_loop(bot))
    expiry_task.add_done_callback(_log_task_failure)
    tasks.append(expiry_task)

    oauth_cleanup_task = asyncio.create_task(run_oauth_state_cleanup_loop())
    oauth_cleanup_task.add_done_callback(_log_task_failure)
    tasks.append(oauth_cleanup_task)

    poll_mode = get_upwork_poll_mode()
    global _UPWORK_POLL_TASK
    if _UPWORK_POLL_TASK is None or _UPWORK_POLL_TASK.done():
        if poll_mode == "rss":
            _UPWORK_POLL_TASK = asyncio.create_task(run_upwork_rss_poller(bot))
            logger.info("poller started: mode=rss")
        else:
            _UPWORK_POLL_TASK = asyncio.create_task(run_upwork_api_poller(bot))
            logger.info("poller started: mode=api")
        _UPWORK_POLL_TASK.add_done_callback(_log_task_failure)
    tasks.append(_UPWORK_POLL_TASK)

    logger.info("Starting bot polling")
    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        _UPWORK_POLL_TASK = None
        db.checkpoint_wal_passive()
        await bot.session.close()
        logger.info("Bot polling stopped")


if __name__ == "__main__":
    asyncio.run(main())

"""Application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher

from app.db import expire_overdue_pro_users, record_error
from app.config import (
    enable_fake_ingestion,
    enable_telegram_ingestion,
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
    start_router,
    subscription_router,
    test_lead_router,
    upgrade_request_router,
)
from app.ingestion.telegram_listener import run_telegram_listener
from app.monitoring import run_monitor_loop
from app.middlewares.rate_limit import RateLimitMiddleware
from app.pipeline import run_fake_ingestion
from app.webhooks import is_stripe_webhook_enabled, run_stripe_webhook_server

BASE_CRITICAL_TABLES = (
    "upgrade_requests",
    "analytics_events",
    "monitor_state",
    "monitor_alerts",
    "error_events",
)
STRIPE_CRITICAL_TABLES = ("payments", "processed_events")
PRO_EXPIRY_CHECK_INTERVAL_SECONDS = 5 * 60


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


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)

    token = load_config()
    stripe_webhook_enabled, _ = is_stripe_webhook_enabled()
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
    dp.message.middleware(RateLimitMiddleware())
    dp.include_router(start_router)
    dp.include_router(skills_router)
    dp.include_router(test_lead_router)
    dp.include_router(subscription_router)
    dp.include_router(plan_router)
    dp.include_router(settings_router)
    dp.include_router(my_id_router)
    dp.include_router(buy_pro_router)
    dp.include_router(payment_status_router)
    dp.include_router(payment_admin_router)
    dp.include_router(analytics_admin_router)
    dp.include_router(monitoring_admin_router)
    dp.include_router(upgrade_request_router)

    tasks: list[asyncio.Task] = []

    if enable_fake_ingestion():
        tasks.append(asyncio.create_task(run_fake_ingestion(bot)))
        logger.info("Fake ingestion enabled")

    if enable_telegram_ingestion():
        telethon_task = asyncio.create_task(run_telegram_listener(bot))
        telethon_task.add_done_callback(_log_task_failure)
        tasks.append(telethon_task)
        logger.info("Telegram ingestion enabled")

    webhook_ready: asyncio.Event | None = asyncio.Event() if stripe_webhook_enabled else None
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

    logger.info("Starting bot polling")
    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        db.checkpoint_wal_passive()
        await bot.session.close()
        logger.info("Bot polling stopped")


if __name__ == "__main__":
    asyncio.run(main())

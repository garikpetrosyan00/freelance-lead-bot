"""Application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher

from app.config import (
    enable_fake_ingestion,
    enable_telegram_ingestion,
    load_config,
)
from app.db import init_db
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
from app.pipeline import run_fake_ingestion
from app.webhooks import is_stripe_webhook_enabled, run_stripe_webhook_server


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
        logging.getLogger(__name__).exception(
            "Background task crashed", exc_info=exc
        )


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)

    token = load_config()
    init_db()

    bot = Bot(token=token)
    dp = Dispatcher()
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

    stripe_webhook_enabled, _ = is_stripe_webhook_enabled()
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

    logger.info("Starting bot polling")
    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await bot.session.close()
        logger.info("Bot polling stopped")


if __name__ == "__main__":
    asyncio.run(main())

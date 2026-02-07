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
    plan_router,
    skills_router,
    start_router,
    subscription_router,
    test_lead_router,
)
from app.ingestion.telegram_listener import run_telegram_listener
from app.pipeline import run_fake_ingestion


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

    tasks: list[asyncio.Task] = []

    if enable_fake_ingestion():
        tasks.append(asyncio.create_task(run_fake_ingestion(bot)))
        logger.info("Fake ingestion enabled")

    if enable_telegram_ingestion():
        telethon_task = asyncio.create_task(run_telegram_listener(bot))
        telethon_task.add_done_callback(_log_task_failure)
        tasks.append(telethon_task)
        logger.info("Telegram ingestion enabled")

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

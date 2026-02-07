"""Application entrypoint."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher

from app.config import load_config
from app.db import init_db
from app.handlers import skills_router, start_router, subscription_router, test_lead_router
from app.pipeline import run_fake_ingestion


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
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

    ingestion_task = asyncio.create_task(run_fake_ingestion(bot))

    logger.info("Starting bot polling")
    try:
        await dp.start_polling(bot)
    finally:
        ingestion_task.cancel()
        with suppress(asyncio.CancelledError):
            await ingestion_task
        await bot.session.close()
        logger.info("Bot polling stopped")


if __name__ == "__main__":
    asyncio.run(main())

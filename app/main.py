"""Application entrypoint."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.config import load_config
from app.db import init_db
from app.handlers import skills_router, start_router


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

    logger.info("Starting bot polling")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot polling stopped")


if __name__ == "__main__":
    asyncio.run(main())

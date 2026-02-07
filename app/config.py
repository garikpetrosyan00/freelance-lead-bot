"""Configuration loading for the bot."""

from __future__ import annotations

import os

from dotenv import load_dotenv


def load_config() -> str:
    """Load configuration and return BOT_TOKEN.

    Raises:
        RuntimeError: If BOT_TOKEN is missing.
    """
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "BOT_TOKEN is not set. Create a .env file or set the BOT_TOKEN environment variable."
        )
    return token

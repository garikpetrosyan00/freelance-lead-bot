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


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip() in {"1", "true", "True", "yes", "YES"}


def get_tg_api_id() -> int:
    api_id = os.getenv("TG_API_ID")
    if not api_id:
        raise RuntimeError("TG_API_ID is not set in environment")
    try:
        return int(api_id)
    except ValueError as exc:
        raise RuntimeError("TG_API_ID must be an integer") from exc


def get_tg_api_hash() -> str:
    api_hash = os.getenv("TG_API_HASH")
    if not api_hash:
        raise RuntimeError("TG_API_HASH is not set in environment")
    return api_hash


def get_tg_source_chats() -> list[str]:
    raw = os.getenv("TG_SOURCE_CHATS", "")
    if not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def enable_fake_ingestion() -> bool:
    return _get_env_bool("ENABLE_FAKE_INGESTION", False)


def enable_telegram_ingestion() -> bool:
    return _get_env_bool("ENABLE_TELEGRAM_INGESTION", True)

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


def get_admin_user_ids() -> set[int]:
    raw = os.getenv("ADMIN_USER_IDS", "")
    if not raw.strip():
        return set()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise RuntimeError("ADMIN_USER_IDS must be a comma-separated list of integers")
        ids.add(int(part))
    return ids


def get_admin_chat_id() -> int | None:
    raw = os.getenv("ADMIN_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("ADMIN_CHAT_ID must be an integer") from exc


def is_admin(user_id: int) -> bool:
    return user_id in get_admin_user_ids()


def enable_fake_ingestion() -> bool:
    return _get_env_bool("ENABLE_FAKE_INGESTION", False)


def enable_telegram_ingestion() -> bool:
    return _get_env_bool("ENABLE_TELEGRAM_INGESTION", True)


def demo_mode_enabled() -> bool:
    return _get_env_bool("DEMO_MODE", False)


def get_support_email() -> str:
    value = os.getenv("SUPPORT_EMAIL", "freelanceleadbot@gmail.com").strip()
    return value or "freelanceleadbot@gmail.com"


def get_payment_link() -> str | None:
    payment_link = os.getenv("PAYMENT_LINK", "").strip()
    return payment_link or None


def get_public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://localhost:8080").strip().rstrip("/")


def get_webhook_host() -> str:
    host = os.getenv("WEBHOOK_HOST", "0.0.0.0").strip()
    return host or "0.0.0.0"


def get_webhook_port() -> int:
    return _get_env_int("WEBHOOK_PORT", 8080)


def get_stripe_secret_key() -> str | None:
    # Canonical var: STRIPE_SECRET. Keep legacy STRIPE_SECRET_KEY for backward compatibility.
    primary = os.getenv("STRIPE_SECRET", "").strip()
    if primary:
        return primary
    legacy = os.getenv("STRIPE_SECRET_KEY", "").strip()
    return legacy or None


def get_stripe_webhook_secret() -> str | None:
    value = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    return value or None


def get_stripe_price_id() -> str | None:
    value = os.getenv("STRIPE_PRICE_ID", "").strip()
    return value or None


def get_stripe_mode() -> str:
    value = os.getenv("STRIPE_MODE", "subscription").strip().lower()
    if value not in {"subscription", "payment"}:
        raise RuntimeError("STRIPE_MODE must be 'subscription' or 'payment'")
    return value


def get_stripe_currency() -> str:
    value = os.getenv("STRIPE_CURRENCY", "usd").strip().lower()
    return value or "usd"


def get_stripe_success_path() -> str:
    value = os.getenv("STRIPE_SUCCESS_PATH", "/stripe/success").strip()
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def get_stripe_cancel_path() -> str:
    value = os.getenv("STRIPE_CANCEL_PATH", "/stripe/cancel").strip()
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def get_lemon_checkout_url() -> str:
    return os.getenv("LEMON_CHECKOUT_URL", "").strip()


def get_lemon_webhook_secret() -> str:
    return os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET", "").strip()

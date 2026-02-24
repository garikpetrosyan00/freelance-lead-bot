"""Configuration loading for the bot."""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency in minimal environments
    def load_dotenv() -> bool:
        return False


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


def get_upwork_rss_poll_seconds() -> int:
    return _get_env_int("UPWORK_RSS_POLL_SECONDS", 180)


def get_upwork_rss_min_matched_skills() -> int:
    return _get_env_int("UPWORK_RSS_MIN_MATCHED_SKILLS", 1)


def get_upwork_rss_free_daily_cap() -> int:
    return _get_env_int("UPWORK_RSS_FREE_DAILY_CAP", 10)


def get_upwork_rss_pro_daily_cap() -> int:
    # Use -1 for unlimited.
    return _get_env_int("UPWORK_RSS_PRO_DAILY_CAP", 100)


def get_upwork_rss_seen_retention_days() -> int:
    return _get_env_int("UPWORK_RSS_SEEN_RETENTION_DAYS", 30)


def get_upwork_rss_seen_prefetch_days() -> int:
    retention_days = max(1, get_upwork_rss_seen_retention_days())
    prefetch_days = _get_env_int("UPWORK_RSS_SEEN_PREFETCH_DAYS", 7)
    if prefetch_days < 1:
        return 1
    return min(prefetch_days, retention_days)


def get_upwork_client_id() -> str:
    value = os.getenv("UPWORK_CLIENT_ID", "").strip()
    if not value:
        raise RuntimeError("UPWORK_CLIENT_ID is not set in environment")
    return value


def get_upwork_client_secret() -> str:
    value = os.getenv("UPWORK_CLIENT_SECRET", "").strip()
    if not value:
        raise RuntimeError("UPWORK_CLIENT_SECRET is not set in environment")
    return value


def get_upwork_redirect_url() -> str:
    value = os.getenv("UPWORK_REDIRECT_URL", "").strip()
    if not value:
        raise RuntimeError("UPWORK_REDIRECT_URL is not set in environment")
    return value


def get_upwork_oauth_authorize_url() -> str:
    value = os.getenv("UPWORK_OAUTH_AUTHORIZE_URL", "").strip()
    return value or "https://www.upwork.com/ab/account-security/oauth2/authorize"


def get_upwork_oauth_token_url() -> str:
    value = os.getenv("UPWORK_OAUTH_TOKEN_URL", "").strip()
    return value or "https://www.upwork.com/api/v3/oauth2/token"


def get_upwork_graphql_url() -> str:
    value = os.getenv("UPWORK_GRAPHQL_URL", "").strip()
    return value or "https://api.upwork.com/graphql"


def get_upwork_public_proxy() -> str | None:
    value = os.getenv("UPWORK_PUBLIC_PROXY", "").strip()
    return value or None


def get_upwork_fetch_mode() -> str:
    value = os.getenv("UPWORK_FETCH_MODE", "public").strip().lower()
    if value not in {"public", "oauth"}:
        raise RuntimeError("UPWORK_FETCH_MODE must be 'public' or 'oauth'")
    return value


def get_upwork_poll_mode() -> str:
    value = os.getenv("UPWORK_POLL_MODE", "api").strip().lower()
    if value not in {"api", "rss"}:
        raise RuntimeError("UPWORK_POLL_MODE must be 'api' or 'rss'")
    return value

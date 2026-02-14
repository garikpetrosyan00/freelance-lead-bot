"""Lightweight logging and sanitization helpers."""

from __future__ import annotations

import re
from typing import Any

from app.ops.secrets import redact_secrets

_SENSITIVE_KEY_PATTERNS = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "session",
    "signature",
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def mask_user_id(user_id: int | str | None) -> str:
    raw = str(user_id or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return "u:***"
    return f"u:***{digits[-4:]}"


def mask_stripe_id(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "-"
    if "_" in raw:
        prefix = raw.split("_", 1)[0]
        return f"{prefix}_***{raw[-4:]}"
    if len(raw) <= 8:
        return f"{raw[:2]}***"
    return f"{raw[:4]}***{raw[-4:]}"


def safe_exc(exc: BaseException) -> str:
    name = exc.__class__.__name__
    text = redact_secrets(str(exc)).strip().replace("\n", " ")
    if len(text) > 180:
        text = text[:180] + "..."
    return f"{name}: {text}" if text else name


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return sanitize_meta(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:20]]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value[:20]]
    if isinstance(value, str):
        text = redact_secrets(value)
        text = _EMAIL_RE.sub("[redacted_email]", text)
        lowered = text.lower()
        if any(key in lowered for key in ("bearer ", "sk_", "whsec_", "xoxb-")):
            return "[redacted]"
        if len(text) > 200:
            return text[:200] + "..."
        return text
    return value


def sanitize_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}

    result: dict[str, Any] = {}
    for key, value in meta.items():
        key_text = str(key)
        lowered = key_text.lower()
        if any(pattern in lowered for pattern in _SENSITIVE_KEY_PATTERNS):
            result[key_text] = "[redacted]"
            continue
        result[key_text] = _sanitize_value(value)
    return result


def log_kv(logger: Any, level: int, msg: str, **kv: Any) -> None:
    try:
        if kv:
            parts = []
            for key in sorted(kv):
                value = kv[key]
                rendered = redact_secrets(str(value)).replace("\n", " ")
                if len(rendered) > 200:
                    rendered = rendered[:200] + "..."
                parts.append(f"{key}={rendered}")
            logger.log(level, "%s | %s", msg, " ".join(parts))
            return
        logger.log(level, msg)
    except Exception:
        # Logging must never break runtime behavior.
        try:
            logger.log(level, msg)
        except Exception:
            return

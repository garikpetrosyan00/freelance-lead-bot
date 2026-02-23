"""Secret redaction helpers for logs and telemetry."""

from __future__ import annotations

import re

_TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_STRIPE_KEY_RE = re.compile(r"\b(?:sk_(?:live|test)|whsec_)[A-Za-z0-9_]+\b")
_BEARER_HEADER_RE = re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_BEARER_VALUE_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")
_JSON_TOKEN_KEY_RE = re.compile(
    r'(?i)(\"(?:access_token|refresh_token)\"\s*:\s*)\"[^\"]*\"'
)
_LONG_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9._~+/=-])(?=[A-Za-z0-9._~+/=-]{40,})(?=[A-Za-z0-9._~+/=-]*[._~+/-])[A-Za-z0-9._~+/=-]+(?![A-Za-z0-9._~+/=-])"
)


def redact_secrets(text: str) -> str:
    raw = text or ""
    redacted = _TELEGRAM_BOT_TOKEN_RE.sub("[redacted_bot_token]", raw)
    redacted = _STRIPE_KEY_RE.sub("[redacted_stripe_key]", redacted)
    redacted = _JSON_TOKEN_KEY_RE.sub(r'\1"[REDACTED_TOKEN]"', redacted)
    redacted = _BEARER_HEADER_RE.sub("Authorization: Bearer [REDACTED_TOKEN]", redacted)
    redacted = _BEARER_VALUE_RE.sub("Bearer [REDACTED_TOKEN]", redacted)
    redacted = _LONG_TOKEN_RE.sub("[REDACTED_TOKEN]", redacted)
    return redacted

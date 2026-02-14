"""Secret redaction helpers for logs and telemetry."""

from __future__ import annotations

import re

_TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_STRIPE_KEY_RE = re.compile(r"\b(?:sk_(?:live|test)|whsec_)[A-Za-z0-9_]+\b")
_BEARER_RE = re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]+")


def redact_secrets(text: str) -> str:
    raw = text or ""
    redacted = _TELEGRAM_BOT_TOKEN_RE.sub("[redacted_bot_token]", raw)
    redacted = _STRIPE_KEY_RE.sub("[redacted_stripe_key]", redacted)
    redacted = _BEARER_RE.sub("Authorization: Bearer [redacted]", redacted)
    return redacted

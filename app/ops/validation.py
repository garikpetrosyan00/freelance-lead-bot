"""Input validation helpers for command handlers."""

from __future__ import annotations

import re

_ALERT_TYPE_RE = re.compile(r"^[a-z0-9_]{1,40}$")


def parse_int(arg: str | None, *, min: int, max: int, default: int | None = None) -> int | None:
    raw = (arg or "").strip()
    if not raw:
        return default
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < min or value > max:
        return None
    return value


def parse_choice(arg: str | None, allowed_set: set[str]) -> str | None:
    raw = (arg or "").strip().upper()
    if not raw:
        return None
    if raw not in allowed_set:
        return None
    return raw


def validate_alert_type(alert_type: str | None) -> str | None:
    raw = (alert_type or "").strip().lower()
    if not raw:
        return None
    if not _ALERT_TYPE_RE.fullmatch(raw):
        return None
    return raw


def validate_limit(limit: int | None, max_default: int) -> int:
    if limit is None:
        return 1
    return max(1, min(int(limit), int(max_default)))

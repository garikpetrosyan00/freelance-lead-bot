"""Lead quality filters."""

from __future__ import annotations

import re
from typing import Tuple

MIN_TEXT_LEN = 60
BLACKLIST_PATTERNS = [
    r"\btest\b",
    r"\bpromo\b",
    r"\bsale\b",
    r"\bdiscount\b",
]


def is_low_quality(text: str) -> tuple[bool, str]:
    cleaned = text.strip()
    if len(cleaned) < MIN_TEXT_LEN:
        return True, "too_short"
    for pattern in BLACKLIST_PATTERNS:
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            return True, "blacklisted"
    return False, "ok"

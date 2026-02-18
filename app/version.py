"""Application version helpers."""

from __future__ import annotations

import os

VERSION = "0.1.0"
BUILD = os.getenv("GIT_SHA", "")[:7]


def version_text() -> str:
    if BUILD:
        return f"Freelance Lead Bot v{VERSION} ({BUILD})"
    return f"Freelance Lead Bot v{VERSION}"

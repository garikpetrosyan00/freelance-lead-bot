"""Smoke checks for support fallback behavior.

Run:
    python3 -m app.scripts.smoke_support_flow
"""

from __future__ import annotations

from app.handlers.support import support_admin_chat_configured, support_delivery_fallback_text


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    fallback = support_delivery_fallback_text()
    _assert("Please email us at" in fallback, "fallback text should instruct email contact")
    _assert("freelanceleadbot@gmail.com" in fallback, "fallback text should include support email")

    _assert(support_admin_chat_configured(None) is False, "None ADMIN_CHAT_ID must be treated as misconfigured")
    _assert(support_admin_chat_configured(0) is False, "0 ADMIN_CHAT_ID must be treated as misconfigured")
    _assert(support_admin_chat_configured(12345) is True, "positive ADMIN_CHAT_ID should be valid")
    print("SMOKE TEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

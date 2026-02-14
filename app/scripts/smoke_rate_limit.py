"""Smoke test for command rate limiting logic."""

from __future__ import annotations

from app.middlewares.rate_limit import (
    DEFAULT_USER_LIMIT_COUNT,
    DEFAULT_USER_LIMIT_WINDOW_SECONDS,
    HEAVY_COMMAND_LIMIT_WINDOW_SECONDS,
    RateLimiter,
)


def main() -> int:
    limiter = RateLimiter()

    user_id = 12345
    base = 1_700_000_000.0

    # Default user command: 10/min allowed, next blocked.
    for idx in range(DEFAULT_USER_LIMIT_COUNT):
        retry = limiter.check(
            user_id=user_id,
            command="plan",
            user_is_admin=False,
            now_ts=base + idx * 0.1,
        )
        assert retry == 0, "unexpected block before threshold"

    blocked = limiter.check(
        user_id=user_id,
        command="plan",
        user_is_admin=False,
        now_ts=base + 1.5,
    )
    assert blocked > 0, "expected rate limit block for default command"

    # Heavy command: 1 per 10s.
    first = limiter.check(
        user_id=user_id,
        command="diag",
        user_is_admin=True,
        now_ts=base,
    )
    second = limiter.check(
        user_id=user_id,
        command="diag",
        user_is_admin=True,
        now_ts=base + 1,
    )
    assert first == 0
    assert second >= 1

    # Outside window should allow again.
    after_window = limiter.check(
        user_id=user_id,
        command="diag",
        user_is_admin=True,
        now_ts=base + HEAVY_COMMAND_LIMIT_WINDOW_SECONDS + 1,
    )
    assert after_window == 0

    # Non-admin admin command gets strict limit (3/min).
    for idx in range(3):
        ok = limiter.check(
            user_id=user_id,
            command="alerts_recent",
            user_is_admin=False,
            now_ts=base + idx,
        )
        assert ok == 0
    blocked_admin_cmd = limiter.check(
        user_id=user_id,
        command="alerts_recent",
        user_is_admin=False,
        now_ts=base + 4,
    )
    assert blocked_admin_cmd > 0

    # Admin gets higher admin-command budget.
    for idx in range(30):
        ok = limiter.check(
            user_id=999,
            command="alerts_recent",
            user_is_admin=True,
            now_ts=base + idx,
        )
        assert ok == 0

    print("smoke_rate_limit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

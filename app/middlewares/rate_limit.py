"""Aiogram v3 middleware for lightweight command throttling."""

from __future__ import annotations

import time
import hashlib
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from app import db
from app.ops.auth import is_admin

DEFAULT_USER_LIMIT_COUNT = 10
DEFAULT_USER_LIMIT_WINDOW_SECONDS = 60
ADMIN_COMMAND_LIMIT_ADMIN_COUNT = 30
ADMIN_COMMAND_LIMIT_ADMIN_WINDOW_SECONDS = 60
ADMIN_COMMAND_LIMIT_NON_ADMIN_COUNT = 3
ADMIN_COMMAND_LIMIT_NON_ADMIN_WINDOW_SECONDS = 60
HEAVY_COMMAND_LIMIT_COUNT = 1
HEAVY_COMMAND_LIMIT_WINDOW_SECONDS = 10
RATE_LIMIT_PERSIST_EVERY = 20

ADMIN_COMMANDS = {
    "set_plan",
    "pro_requests",
    "approve_pro",
    "reject_pro",
    "payments_recent",
    "subs_past_due",
    "force_sync_user",
    "stats_today",
    "stats_7d",
    "funnel_7d",
    "lead_quality_7d",
    "quality_7d",
    "blocks_7d",
    "sources_7d",
    "retention_7d",
    "retention_30d",
    "pro_health_30d",
    "health",
    "diag",
    "errors_recent",
    "alerts_recent",
    "silence",
    "unsilence",
}

HEAVY_COMMANDS = {"diag", "retention_30d", "sources_7d"}


class RateLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[int, str], deque[float]] = defaultdict(deque)
        self._event_counter = 0

    def _limits(self, command: str, user_is_admin: bool) -> tuple[int, int]:
        if command in HEAVY_COMMANDS:
            return HEAVY_COMMAND_LIMIT_COUNT, HEAVY_COMMAND_LIMIT_WINDOW_SECONDS
        if command in ADMIN_COMMANDS:
            if user_is_admin:
                return ADMIN_COMMAND_LIMIT_ADMIN_COUNT, ADMIN_COMMAND_LIMIT_ADMIN_WINDOW_SECONDS
            return ADMIN_COMMAND_LIMIT_NON_ADMIN_COUNT, ADMIN_COMMAND_LIMIT_NON_ADMIN_WINDOW_SECONDS
        return DEFAULT_USER_LIMIT_COUNT, DEFAULT_USER_LIMIT_WINDOW_SECONDS

    def check(self, *, user_id: int, command: str, user_is_admin: bool, now_ts: float | None = None) -> int:
        now = now_ts if now_ts is not None else time.time()
        limit_count, window_seconds = self._limits(command, user_is_admin)
        bucket = self._events[(user_id, command)]
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= limit_count:
            retry_after = max(1, int(bucket[0] + window_seconds - now))
            self._persist_rate_limit(user_id=user_id, command=command, denied=True)
            return retry_after

        bucket.append(now)
        self._event_counter += 1
        if self._event_counter % RATE_LIMIT_PERSIST_EVERY == 0:
            self._persist_rate_limit(user_id=user_id, command=command, denied=False)
        return 0

    def _persist_rate_limit(self, *, user_id: int, command: str, denied: bool) -> None:
        try:
            scope = f"{user_id}:{command}:{'deny' if denied else 'ok'}"
            digest = hashlib.sha1(scope.encode("utf-8")).hexdigest()
            key = f"rl:{digest}"
            now_iso = db._utc_now()
            with db._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO monitor_state(key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    (key, now_iso, now_iso),
                )
                conn.commit()
        except Exception:
            return


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        super().__init__()
        self._limiter = RateLimiter()

    @staticmethod
    def _extract_command(message: Message) -> str | None:
        text = (message.text or "").strip()
        if not text.startswith("/"):
            return None
        first = text.split()[0][1:]
        if not first:
            return None
        command = first.split("@", 1)[0].strip().lower()
        return command or None

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        command = self._extract_command(event)
        if command is None:
            return await handler(event, data)

        user = event.from_user
        if user is None:
            await event.answer("Unauthorized")
            return None

        retry_after = self._limiter.check(
            user_id=int(user.id),
            command=command,
            user_is_admin=is_admin(int(user.id)),
        )
        if retry_after > 0:
            await event.answer(f"Too many requests, try again in {retry_after}s")
            return None

        return await handler(event, data)

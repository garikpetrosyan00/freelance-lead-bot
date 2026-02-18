"""Soft paywall teaser messages for blocked FREE users."""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from datetime import date
from typing import TYPE_CHECKING, Any

from app.analytics import log_event
from app.leads import Lead

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import InlineKeyboardMarkup
else:  # pragma: no cover - optional import for smoke scripts without aiogram
    Bot = Any
    InlineKeyboardMarkup = Any

TEASER_DAILY_LIMIT = 2
TEASER_REASON_MIN_LEVEL = "min_level"
TEASER_REASON_CAP = "cap"
TEASER_REASON_QUOTA = "quota"
TEASER_REASON_OTHER = "other"
TEASER_ALLOWED_REASONS = {
    TEASER_REASON_MIN_LEVEL,
    TEASER_REASON_CAP,
    TEASER_REASON_QUOTA,
    TEASER_REASON_OTHER,
}
_FALLBACK_DAY = ""
_FALLBACK_COUNTERS: dict[int, int] = {}


def _today_iso() -> str:
    return date.today().isoformat()


def _normalize_line(text: str, max_len: int = 120) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return f"{cleaned[: max_len - 3].rstrip()}..."


def _reset_fallback_if_needed(today_iso: str) -> None:
    global _FALLBACK_DAY, _FALLBACK_COUNTERS
    if _FALLBACK_DAY == today_iso:
        return
    _FALLBACK_DAY = today_iso
    _FALLBACK_COUNTERS = {}


def _fallback_count(user_id: int, today_iso: str) -> int:
    _reset_fallback_if_needed(today_iso)
    return int(_FALLBACK_COUNTERS.get(user_id, 0))


def _fallback_increment(user_id: int, today_iso: str) -> None:
    _reset_fallback_if_needed(today_iso)
    _FALLBACK_COUNTERS[user_id] = _fallback_count(user_id, today_iso) + 1


def _analytics_teaser_count_today(db_module, user_id: int, today_iso: str) -> int | None:
    db_path = getattr(db_module, "DB_PATH", None)
    db_uri = bool(getattr(db_module, "DB_URI", False))
    if not db_path:
        return None
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path, uri=db_uri)
        table_row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='analytics_events' LIMIT 1"
        ).fetchone()
        if not table_row:
            return None
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM analytics_events
            WHERE user_id = ?
              AND event = 'teaser_sent'
              AND substr(ts, 1, 10) = ?
            """,
            (user_id, today_iso),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return None
    finally:
        if conn is not None:
            with suppress(Exception):
                conn.close()


def _teaser_markup() -> InlineKeyboardMarkup:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Upgrade to PRO", callback_data="ui:upgrade")],
            [InlineKeyboardButton(text="📊 Usage", callback_data="ui:usage")],
        ]
    )


def _normalize_reason(reason: str) -> str:
    normalized = str(reason or "").strip().lower()
    if normalized in TEASER_ALLOWED_REASONS:
        return normalized
    return TEASER_REASON_OTHER


def _teaser_text(lead: Lead, match_level: str, reason: str) -> str:
    preview_source = lead.description or lead.title
    preview = _normalize_line(preview_source, max_len=120)
    title = _normalize_line(lead.title, max_len=80)
    normalized_reason = _normalize_reason(reason)
    value_line = (
        "PRO unlocks LOW leads + more matches."
        if normalized_reason == TEASER_REASON_MIN_LEVEL
        else "PRO gives unlimited daily leads and unlocks more leads."
    )
    return (
        "🔒 Lead locked\n"
        f"Level: {match_level}\n"
        f"Title: {title}\n"
        f"Preview: {preview}\n"
        f"{value_line}\n"
        "FREE: 3 previews/day. PRO: Unlimited."
    )


async def maybe_send_teaser(
    bot: Bot,
    db,
    user_id: int,
    chat_id: int,
    lead: Lead,
    reason: str,
    match_level: str,
) -> None:
    reason = _normalize_reason(reason)

    try:
        if db.get_plan(user_id) != "FREE":
            return
    except Exception:
        return

    today_iso = _today_iso()
    count = _analytics_teaser_count_today(db, user_id, today_iso)
    using_fallback = count is None
    if using_fallback:
        count = _fallback_count(user_id, today_iso)
    if (count or 0) >= TEASER_DAILY_LIMIT:
        return

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=_teaser_text(lead, str(match_level or "NONE"), reason),
            reply_markup=_teaser_markup(),
        )
    except Exception as exc:
        with suppress(Exception):
            db.record_error(
                "monetization",
                exc,
                context={"user_id": user_id, "action": "send_teaser", "reason": reason},
            )
        return

    if using_fallback:
        _fallback_increment(user_id, today_iso)
        return

    log_event(
        "teaser_sent",
        user_id=user_id,
        plan="FREE",
        match_level=str(match_level or ""),
        meta={"reason": reason, "source": lead.source},
    )

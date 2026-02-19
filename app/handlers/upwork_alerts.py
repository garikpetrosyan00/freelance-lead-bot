"""Commands for managing Upwork RSS alerts."""

from __future__ import annotations

from urllib.parse import urlparse

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app import db
from app.config import get_upwork_rss_free_daily_cap, get_upwork_rss_pro_daily_cap
from app.jobs.upwork_rss import fetch_feed_items

router = Router()


def _short_url(url: str, limit: int = 72) -> str:
    parsed = urlparse((url or "").strip())
    value = f"{parsed.netloc}{parsed.path}" if parsed.netloc else (url or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _is_valid_upwork_rss_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw.startswith("https://"):
        return False
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    lowered = raw.lower()
    if "upwork.com" not in host:
        return False
    if "/rss" not in lowered and "feed" not in lowered:
        return False
    return bool(parsed.netloc)


def _parse_add_rss_args(text: str) -> tuple[str | None, str | None]:
    raw = (text or "").strip()
    if not raw:
        return None, None
    _, _, rest = raw.partition(" ")
    payload = rest.strip()
    if not payload:
        return None, None
    if "|" in payload:
        url_raw, title_raw = payload.split("|", 1)
        rss_url = url_raw.strip()
        title = title_raw.strip() or None
        return rss_url or None, title
    parts = payload.split(maxsplit=1)
    rss_url = parts[0].strip() if parts else ""
    title = parts[1].strip() if len(parts) > 1 else None
    return rss_url or None, title


@router.message(Command("upwork_add_rss"))
async def handle_upwork_add_rss(message: Message) -> None:
    rss_url, title = _parse_add_rss_args(message.text or "")
    if not rss_url:
        await message.answer("Usage: /upwork_add_rss <rss_url> [title] or /upwork_add_rss <rss_url> | <title>")
        return

    if not _is_valid_upwork_rss_url(rss_url):
        await message.answer("Invalid URL. Use an https Upwork RSS/feed link on upwork.com.")
        return

    created = db.add_upwork_feed(message.from_user.id, rss_url, title=title)
    await message.answer("Added." if created else "Feed already existed. Re-enabled and updated.")


@router.message(Command("upwork_feeds"))
async def handle_upwork_feeds(message: Message) -> None:
    feeds = db.list_upwork_feeds(message.from_user.id)
    if not feeds:
        await message.answer("No feeds yet. Add one with /upwork_add_rss <rss_url> [title]")
        return

    lines = ["Your Upwork RSS feeds:"]
    for feed in feeds:
        enabled_text = "ON" if int(feed["enabled"]) == 1 else "OFF"
        title = str(feed.get("title") or "-")
        lines.append(f"{feed['id']} [{enabled_text}] {title} | {_short_url(str(feed['rss_url']))}")
    await message.answer("\n".join(lines))


def _parse_feed_id(text: str) -> int | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    raw = parts[1].strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _parse_keywords_csv(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


@router.message(Command("upwork_feed_disable"))
async def handle_upwork_feed_disable(message: Message) -> None:
    feed_id = _parse_feed_id(message.text or "")
    if feed_id is None:
        await message.answer("Usage: /upwork_feed_disable <id>")
        return
    feed = db.get_upwork_feed(message.from_user.id, feed_id)
    if not feed:
        await message.answer("Feed not found for this account.")
        return
    updated = db.set_upwork_feed_enabled(message.from_user.id, feed_id, enabled=False)
    await message.answer("Disabled." if updated else "Unable to disable feed right now.")


@router.message(Command("upwork_feed_enable"))
async def handle_upwork_feed_enable(message: Message) -> None:
    feed_id = _parse_feed_id(message.text or "")
    if feed_id is None:
        await message.answer("Usage: /upwork_feed_enable <id>")
        return
    feed = db.get_upwork_feed(message.from_user.id, feed_id)
    if not feed:
        await message.answer("Feed not found for this account.")
        return
    updated = db.set_upwork_feed_enabled(message.from_user.id, feed_id, enabled=True)
    await message.answer("Enabled." if updated else "Unable to enable feed right now.")


@router.message(Command("upwork_feed_delete"))
async def handle_upwork_feed_delete(message: Message) -> None:
    feed_id = _parse_feed_id(message.text or "")
    if feed_id is None:
        await message.answer("Usage: /upwork_feed_delete <id>")
        return
    feed = db.get_upwork_feed(message.from_user.id, feed_id)
    if not feed:
        await message.answer("Feed not found for this account.")
        return
    deleted = db.delete_upwork_feed(message.from_user.id, feed_id)
    await message.answer("Deleted." if deleted else "Unable to delete feed right now.")


@router.message(Command("upwork_test_rss"))
async def handle_upwork_test_rss(message: Message) -> None:
    feed_id = _parse_feed_id(message.text or "")
    if feed_id is None:
        await message.answer("Usage: /upwork_test_rss <id>")
        return

    feed = db.get_upwork_feed(message.from_user.id, feed_id)
    if not feed:
        await message.answer("Feed not found for this account.")
        return

    try:
        items = await fetch_feed_items(str(feed["rss_url"]))
    except Exception as exc:
        db.record_error(
            "upwork_rss",
            exc,
            context={"user_id": message.from_user.id, "feed_id": feed_id, "action": "test_rss"},
        )
        await message.answer("Feed fetch failed. Check URL and try again.")
        return

    if not items:
        await message.answer("No items found in this feed.")
        return

    lines = [f"Top {min(3, len(items))} items:"]
    for item in items[:3]:
        lines.append(f"- {item.title}\n{item.link}")
    await message.answer("\n".join(lines), disable_web_page_preview=True)


@router.message(Command("upwork_mute"))
async def handle_upwork_mute(message: Message) -> None:
    text = (message.text or "").strip()
    _, _, raw_keywords = text.partition(" ")
    keywords = _parse_keywords_csv(raw_keywords)
    if not keywords:
        await message.answer("Usage: /upwork_mute keyword1, keyword2, keyword3")
        return
    saved = db.set_upwork_mute_keywords(message.from_user.id, keywords)
    await message.answer(
        f"Muted keywords saved: {', '.join(saved)}" if saved else "Mute list is now empty."
    )


@router.message(Command("upwork_unmute"))
async def handle_upwork_unmute(message: Message) -> None:
    text = (message.text or "").strip()
    _, _, raw_keyword = text.partition(" ")
    target = raw_keyword.strip().lower()
    if not target:
        await message.answer("Usage: /upwork_unmute <keyword>")
        return
    prefs = db.get_upwork_user_prefs(message.from_user.id)
    current = [value for value in (prefs.get("mute_keywords") or []) if value.lower() != target]
    saved = db.set_upwork_mute_keywords(message.from_user.id, current)
    await message.answer(
        f"Updated mute keywords: {', '.join(saved)}" if saved else "Muted keywords cleared."
    )


@router.message(Command("upwork_mute_clear"))
async def handle_upwork_mute_clear(message: Message) -> None:
    db.set_upwork_mute_keywords(message.from_user.id, [])
    await message.answer("Muted keywords cleared.")


@router.message(Command("upwork_digest"))
async def handle_upwork_digest(message: Message) -> None:
    text = (message.text or "").strip().lower()
    _, _, raw_value = text.partition(" ")
    if raw_value not in {"on", "off"}:
        await message.answer("Usage: /upwork_digest on|off")
        return
    enabled = raw_value == "on"
    db.set_upwork_digest_mode(message.from_user.id, enabled)
    await message.answer(f"Digest mode {'enabled' if enabled else 'disabled'}.")


@router.message(Command("upwork_prefs"))
async def handle_upwork_prefs(message: Message) -> None:
    user_id = message.from_user.id
    prefs = db.get_upwork_user_prefs(user_id)
    plan = db.get_plan(user_id)
    if plan == "PRO":
        cap = get_upwork_rss_pro_daily_cap()
    else:
        cap = get_upwork_rss_free_daily_cap()
    cap_text = "Unlimited" if cap < 0 else str(cap)
    muted = list(prefs.get("mute_keywords") or [])
    muted_text = ", ".join(muted) if muted else "-"
    digest_text = "ON" if int(prefs.get("digest_mode") or 0) == 1 else "OFF"
    lines = [
        "Upwork preferences:",
        f"Muted keywords: {muted_text}",
        f"Digest mode: {digest_text}",
        f"Plan: {plan}",
        f"Daily cap: {cap_text}",
    ]
    await message.answer("\n".join(lines))

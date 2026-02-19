"""Background poller for Upwork RSS alerts."""

from __future__ import annotations

import asyncio
import html
import logging
import random
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

try:
    from aiogram.exceptions import TelegramRetryAfter
except Exception:  # pragma: no cover - depends on installed aiogram version
    TelegramRetryAfter = None  # type: ignore[assignment]

from app import db
from app.config import (
    get_upwork_rss_free_daily_cap,
    get_upwork_rss_min_matched_skills,
    get_upwork_rss_poll_seconds,
    get_upwork_rss_pro_daily_cap,
    get_upwork_rss_seen_prefetch_days,
    get_upwork_rss_seen_retention_days,
)
from app.jobs.matching import match_skills, normalize
from app.jobs.upwork_rss import JobItem, fetch_feed_items

logger = logging.getLogger(__name__)
_YEREVAN_TZ = ZoneInfo("Asia/Yerevan")
_FEED_CONCURRENCY = 5
_CLEANUP_INTERVAL_SECONDS = 3600
_DAILY_COUNTER_RETENTION_DAYS = 60


def _sanitize_feed_url(url: str) -> str:
    split = urlsplit(url)
    return urlunsplit((split.scheme, split.netloc, split.path, "", ""))


def _day_key_now() -> str:
    return datetime.now(_YEREVAN_TZ).strftime("%Y-%m-%d")


def _daily_cap_for_plan(plan: str) -> int | None:
    normalized = (plan or "").upper().strip()
    if normalized == "PRO":
        cap = get_upwork_rss_pro_daily_cap()
    else:
        cap = get_upwork_rss_free_daily_cap()
    if cap < 0:
        return None
    return cap


def _message_text(item: JobItem, score: int, matched: list[str]) -> str:
    escaped_title = html.escape(item.title or "Untitled")
    snippet = html.escape(item.summary or "No summary")
    matched_text = html.escape(", ".join(matched[:10])) if matched else "none"
    link = html.escape(item.link or "")
    return (
        f"<b>{escaped_title}</b>\n"
        f"{snippet}\n"
        f"<a href=\"{link}\">Open on Upwork</a>\n"
        f"Matched: {matched_text}\n"
        f"Score: {score}%"
    )


def _digest_text(rows: list[tuple[JobItem, int, list[str]]]) -> str:
    shown = rows[:10]
    lines = [f"🧾 Upwork matches ({len(rows)})"]
    for item, _score, matched in shown:
        safe_title = html.escape(item.title or "Untitled")
        safe_link = html.escape(item.link or "")
        matched_text = html.escape(", ".join(matched[:3])) if matched else "none"
        lines.append(f"• <a href=\"{safe_link}\">{safe_title}</a> [{matched_text}]")
    if len(rows) > len(shown):
        lines.append(f"… and {len(rows) - len(shown)} more")
    return "\n".join(lines)


def _is_muted(job_text: str, mute_keywords: list[str]) -> bool:
    if not mute_keywords:
        return False
    normalized_job = f" {normalize(job_text)} "
    for keyword in mute_keywords:
        cleaned = normalize(keyword)
        if not cleaned:
            continue
        if f" {cleaned} " in normalized_job:
            return True
    return False


def run_upwork_retention_cleanup(
    seen_retention_days: int,
    daily_counter_retention_days: int = _DAILY_COUNTER_RETENTION_DAYS,
) -> tuple[int, int]:
    now_utc = datetime.now(timezone.utc)
    seen_cutoff_iso = (now_utc - timedelta(days=max(1, int(seen_retention_days)))).isoformat()
    counters_cutoff_day = (now_utc - timedelta(days=max(1, int(daily_counter_retention_days)))).strftime("%Y-%m-%d")
    removed_seen = db.cleanup_upwork_jobs_seen_before(seen_cutoff_iso)
    removed_counters = db.cleanup_upwork_daily_counters_before(counters_cutoff_day)
    return removed_seen, removed_counters


async def run_upwork_rss_poller(bot: Bot) -> None:
    interval_seconds = max(60, get_upwork_rss_poll_seconds())
    min_matched_skills = max(1, get_upwork_rss_min_matched_skills())
    seen_retention_days = max(1, get_upwork_rss_seen_retention_days())
    seen_prefetch_days = max(1, get_upwork_rss_seen_prefetch_days())
    semaphore = asyncio.Semaphore(_FEED_CONCURRENCY)
    loop = asyncio.get_running_loop()
    next_allowed_fetch_at: dict[int, float] = {}
    fail_counts: dict[int, int] = {}
    next_cleanup_at = loop.time() + _CLEANUP_INTERVAL_SECONDS

    logger.info("Upwork RSS poller started (interval=%ss)", interval_seconds)

    async def _send_alert(user_id: int, feed_id: int, text: str) -> bool:
        try:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return True
        except asyncio.CancelledError:
            raise
        except TelegramForbiddenError:
            logger.info("Upwork RSS send blocked by user feed_id=%s user_id=%s", feed_id, user_id)
            return False
        except TelegramBadRequest as exc:
            # Covers cases like "chat not found".
            if "chat not found" in str(exc).lower():
                logger.info("Upwork RSS send failed (chat not found) feed_id=%s user_id=%s", feed_id, user_id)
                return False
            db.record_error(
                "upwork_rss",
                exc,
                context={"user_id": user_id, "feed_id": feed_id, "action": "send_alert"},
            )
            return False
        except Exception as exc:
            if TelegramRetryAfter is not None and isinstance(exc, TelegramRetryAfter):
                retry_after = int(getattr(exc, "retry_after", 1) or 1)
                await asyncio.sleep(max(1, min(retry_after, 30)))
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    return True
                except asyncio.CancelledError:
                    raise
                except Exception as retry_exc:
                    db.record_error(
                        "upwork_rss",
                        retry_exc,
                        context={"user_id": user_id, "feed_id": feed_id, "action": "send_alert_retry"},
                    )
                    return False
            db.record_error(
                "upwork_rss",
                exc,
                context={"user_id": user_id, "feed_id": feed_id, "action": "send_alert"},
            )
            logger.warning(
                "Upwork RSS send failed feed_id=%s user_id=%s",
                feed_id,
                user_id,
                exc_info=True,
            )
            return False

    async def process_feed(
        feed: dict[str, object],
        digest_batches: dict[int, list[tuple[JobItem, int, list[str]]]],
        cycle_alert_counts: dict[int, int],
        prefs_cache: dict[int, dict[str, object]],
        cycle_seen: set[tuple[int, str]],
        prefetched_seen_cache: dict[int, set[str] | None],
        seen_prefetch_cutoff_iso: str,
    ) -> None:
        feed_id = int(feed["id"])
        user_id = int(feed["user_id"])
        rss_url = str(feed["rss_url"])
        safe_url = _sanitize_feed_url(rss_url)

        now_mono = loop.time()
        if now_mono < next_allowed_fetch_at.get(feed_id, 0.0):
            return

        async with semaphore:
            try:
                items = await fetch_feed_items(rss_url)
            except asyncio.CancelledError:
                raise
            except Exception:
                fails = fail_counts.get(feed_id, 0) + 1
                fail_counts[feed_id] = fails
                backoff = min(30 * (2 ** fails), 900)
                jitter = random.uniform(0, 3)
                next_allowed_fetch_at[feed_id] = now_mono + float(backoff) + jitter
                logger.warning(
                    "Upwork RSS fetch failed feed_id=%s user_id=%s url=%s fails=%s",
                    feed_id,
                    user_id,
                    safe_url,
                    fails,
                    exc_info=True,
                )
                return

            fail_counts[feed_id] = 0
            next_allowed_fetch_at[feed_id] = now_mono

            for item in items:
                try:
                    if not item.link or not item.uid:
                        continue

                    cycle_key = (user_id, item.uid)
                    if cycle_key in cycle_seen:
                        continue

                    seen_uids = prefetched_seen_cache.get(user_id)
                    if seen_uids is None and user_id not in prefetched_seen_cache:
                        try:
                            seen_uids = set(db.list_upwork_jobs_seen_since(user_id, seen_prefetch_cutoff_iso))
                            prefetched_seen_cache[user_id] = seen_uids
                        except Exception as prefetch_exc:
                            db.record_error(
                                "upwork_rss",
                                prefetch_exc,
                                context={"action": "prefetch_seen", "user_id": user_id},
                            )
                            prefetched_seen_cache[user_id] = None
                            seen_uids = None

                    if seen_uids is not None:
                        if item.uid in seen_uids:
                            cycle_seen.add(cycle_key)
                            continue
                    elif db.is_upwork_job_seen(user_id, item.uid):
                        cycle_seen.add(cycle_key)
                        continue

                    prefs = prefs_cache.get(user_id)
                    if prefs is None:
                        prefs = db.get_upwork_user_prefs(user_id)
                        prefs_cache[user_id] = prefs

                    search_text = " ".join([item.title or "", item.summary or ""])
                    mute_keywords = list(prefs.get("mute_keywords") or [])
                    if _is_muted(search_text, mute_keywords):
                        cycle_seen.add(cycle_key)
                        continue

                    skills = db.get_skills(user_id)
                    if not skills:
                        consumed = db.mark_upwork_job_seen(user_id, item.uid)
                        if consumed and seen_uids is not None:
                            seen_uids.add(item.uid)
                        cycle_seen.add(cycle_key)
                        continue

                    score, matched = match_skills(search_text, skills)
                    if len(matched) < min_matched_skills:
                        consumed = db.mark_upwork_job_seen(user_id, item.uid)
                        if consumed and seen_uids is not None:
                            seen_uids.add(item.uid)
                        cycle_seen.add(cycle_key)
                        continue

                    day_key = _day_key_now()
                    plan = db.get_plan(user_id)
                    cap = _daily_cap_for_plan(plan)
                    sent_today = db.get_upwork_daily_alerts_sent(user_id, day_key)
                    reserved = cycle_alert_counts.get(user_id, 0)
                    if cap is not None and (sent_today + reserved) >= cap:
                        consumed = db.mark_upwork_job_seen(user_id, item.uid)
                        if consumed and seen_uids is not None:
                            seen_uids.add(item.uid)
                        cycle_seen.add(cycle_key)
                        continue

                    digest_mode = int(prefs.get("digest_mode") or 0) == 1
                    if digest_mode:
                        consumed = db.mark_upwork_job_seen(user_id, item.uid)
                        if consumed and seen_uids is not None:
                            seen_uids.add(item.uid)
                        cycle_seen.add(cycle_key)
                        if not consumed:
                            continue
                        digest_batches.setdefault(user_id, []).append((item, score, matched))
                        cycle_alert_counts[user_id] = reserved + 1
                        continue

                    consumed = db.mark_upwork_job_seen(user_id, item.uid)
                    if consumed and seen_uids is not None:
                        seen_uids.add(item.uid)
                    cycle_seen.add(cycle_key)
                    if not consumed:
                        continue
                    sent = await _send_alert(user_id, feed_id, _message_text(item, score, matched))
                    if sent:
                        db.inc_upwork_daily_alerts_sent(user_id, day_key, delta=1)
                        cycle_alert_counts[user_id] = reserved + 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    db.record_error(
                        "upwork_rss",
                        exc,
                        context={"user_id": user_id, "feed_id": feed_id, "action": "process_item"},
                    )
                    logger.warning(
                        "Upwork RSS item processing failed feed_id=%s user_id=%s",
                        feed_id,
                        user_id,
                        exc_info=True,
                    )

    try:
        while True:
            digest_batches: dict[int, list[tuple[JobItem, int, list[str]]]] = {}
            cycle_alert_counts: dict[int, int] = {}
            prefs_cache: dict[int, dict[str, object]] = {}
            cycle_seen: set[tuple[int, str]] = set()
            prefetched_seen_cache: dict[int, set[str] | None] = {}
            seen_prefetch_cutoff_iso = (
                datetime.now(timezone.utc) - timedelta(days=seen_prefetch_days)
            ).isoformat()
            try:
                feeds = db.list_enabled_upwork_feeds()
                if feeds:
                    await asyncio.gather(
                        *(
                            process_feed(
                                feed,
                                digest_batches,
                                cycle_alert_counts,
                                prefs_cache,
                                cycle_seen,
                                prefetched_seen_cache,
                                seen_prefetch_cutoff_iso,
                            )
                            for feed in feeds
                        )
                    )

                for user_id, rows in digest_batches.items():
                    if not rows:
                        continue
                    sent = await _send_alert(user_id, 0, _digest_text(rows))
                    if sent:
                        db.inc_upwork_daily_alerts_sent(user_id, _day_key_now(), delta=len(rows))

                if loop.time() >= next_cleanup_at:
                    try:
                        run_upwork_retention_cleanup(seen_retention_days)
                    except Exception as cleanup_exc:
                        db.record_error("upwork_rss", cleanup_exc, context={"action": "retention_cleanup"})
                        logger.warning("Upwork RSS cleanup failed", exc_info=True)
                    finally:
                        next_cleanup_at = loop.time() + _CLEANUP_INTERVAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                db.record_error("upwork_rss", exc, context={"action": "poll_cycle"})
                logger.warning("Upwork RSS poll cycle failed", exc_info=True)

            jitter = random.randint(0, max(5, interval_seconds // 8))
            await asyncio.sleep(interval_seconds + jitter)
    except asyncio.CancelledError:
        logger.info("Upwork RSS poller stopped")
        raise

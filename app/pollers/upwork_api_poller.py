"""Background poller for Upwork API search profiles."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from aiogram import Bot

from app import db
from app.config import (
    get_upwork_rss_free_daily_cap,
    get_upwork_rss_min_matched_skills,
    get_upwork_rss_poll_seconds,
    get_upwork_rss_pro_daily_cap,
    get_upwork_rss_seen_retention_days,
)
from app.integrations.upwork.client import UpworkAuthError
from app.jobs.matching import match_skills, normalize

logger = logging.getLogger(__name__)
_YEREVAN_TZ = ZoneInfo("Asia/Yerevan")
_API_CONCURRENCY = 4
_CLEANUP_INTERVAL_SECONDS = 3600
_BACKOFF_BASE_SECONDS = (30.0, 60.0, 120.0)
_DAILY_COUNTER_RETENTION_DAYS = 60
_AUTH_NOTIFY_COOLDOWN_SECONDS = 6 * 3600

SearchFn = Callable[[int, str, int], Awaitable[list[dict[str, Any]]]]
SendFn = Callable[[int, str, Any | None], Awaitable[None]]


@dataclass(slots=True)
class _PollerState:
    next_allowed_by_profile: dict[tuple[int, int], float] = field(default_factory=dict)
    fail_counts_by_profile: dict[tuple[int, int], int] = field(default_factory=dict)
    next_cleanup_at: float = 0.0
    last_auth_notify_at: dict[int, float] = field(default_factory=dict)


def _day_key_now() -> str:
    return datetime.now(_YEREVAN_TZ).strftime("%Y-%m-%d")


def _daily_cap_for_plan(plan: str) -> int | None:
    normalized = (plan or "").upper().strip()
    cap = get_upwork_rss_pro_daily_cap() if normalized == "PRO" else get_upwork_rss_free_daily_cap()
    if cap < 0:
        return None
    return cap


def _is_muted(job_text: str, mute_keywords: list[str]) -> bool:
    if not mute_keywords:
        return False
    normalized_job = f" {normalize(job_text)} "
    for keyword in mute_keywords:
        cleaned = normalize(keyword)
        if cleaned and f" {cleaned} " in normalized_job:
            return True
    return False


def _build_job_uid(job: dict[str, Any]) -> str:
    raw_id = str(job.get("id") or "").strip()
    if raw_id:
        return raw_id
    payload = "|".join(
        [
            str(job.get("title") or "").strip(),
            str(job.get("url") or "").strip(),
            str(job.get("published_at") or "").strip(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_iso_utc(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_profile_cursor(raw_cursor: str | None) -> tuple[datetime | None, str | None]:
    raw = str(raw_cursor or "").strip()
    if not raw:
        return None, None
    try:
        payload = json.loads(raw)
    except Exception:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    last_published_at = _parse_iso_utc(str(payload.get("last_published_at") or "") or None)
    last_job_id_raw = payload.get("last_job_id")
    last_job_id = str(last_job_id_raw).strip() if last_job_id_raw is not None else None
    if last_job_id == "":
        last_job_id = None
    return last_published_at, last_job_id


def _watermark_tuple(published_at: datetime | None, job_id: str | None) -> tuple[datetime, str]:
    ts = published_at or datetime.min.replace(tzinfo=timezone.utc)
    return ts, str(job_id or "")


def _job_is_newer_than_cursor(
    job: dict[str, Any],
    last_published_at: datetime | None,
    last_job_id: str | None,
) -> bool:
    job_published_at = _parse_iso_utc(str(job.get("published_at") or "") or None)
    job_id = str(job.get("id") or "").strip() or None
    if job_published_at is None:
        # Keep undated jobs; cursor logic cannot safely compare them.
        return True
    if last_published_at is None:
        return True
    if job_published_at > last_published_at:
        return True
    if job_published_at < last_published_at:
        return False
    if not job_id or not last_job_id:
        return False
    return job_id > last_job_id


def _max_watermark_from_jobs(jobs: list[dict[str, Any]]) -> tuple[datetime | None, str | None]:
    best_dt: datetime | None = None
    best_job_id: str | None = None
    for job in jobs:
        job_dt = _parse_iso_utc(str(job.get("published_at") or "") or None)
        if job_dt is None:
            continue
        job_id = str(job.get("id") or "").strip() or None
        if _watermark_tuple(job_dt, job_id) > _watermark_tuple(best_dt, best_job_id):
            best_dt = job_dt
            best_job_id = job_id
    return best_dt, best_job_id


def _item_alert_message(job: dict[str, Any], matched: list[str]) -> tuple[str, Any | None]:
    title = str(job.get("title") or "Untitled")
    snippet = str(job.get("snippet") or "")
    url = str(job.get("url") or "") or None
    try:
        from app.jobs.formatting import format_lead_message

        return format_lead_message(title, snippet, url, matched, "upwork_api")
    except Exception:
        lines = [
            "🔥 New lead found!",
            f"Title: {title}",
            f"Summary:\n{snippet or '-'}",
            f"Link: {url}" if url else None,
            f"Matched skills: {', '.join(matched[:10])}" if matched else "Matched: 0 skills",
            "Source: upwork_api",
        ]
        return "\n".join(line for line in lines if line), None


def _digest_text(rows: list[tuple[dict[str, Any], int, list[str]]]) -> str:
    shown = rows[:10]
    lines = [f"🧾 Upwork matches ({len(rows)})"]
    for item, _score, matched in shown:
        safe_title = str(item.get("title") or "Untitled")
        safe_link = str(item.get("url") or "")
        matched_text = ", ".join(matched[:3]) if matched else "none"
        if safe_link:
            lines.append(f"• {safe_title} [{matched_text}]\n{safe_link}")
        else:
            lines.append(f"• {safe_title} [{matched_text}]")
    if len(rows) > len(shown):
        lines.append(f"… and {len(rows) - len(shown)} more")
    return "\n".join(lines)


def _filters_match(job: dict[str, Any], filters_json: str | None) -> bool:
    if not filters_json:
        return True
    try:
        filters = json.loads(filters_json)
    except Exception:
        return True
    if not isinstance(filters, dict):
        return True

    if "client_payment_verified" in filters:
        expected = bool(filters.get("client_payment_verified"))
        actual = bool(job.get("client_payment_verified"))
        if actual != expected:
            return False
    if "budget_min" in filters:
        try:
            budget_min = float(filters.get("budget_min"))
            budget = job.get("budget")
            if budget is None or float(budget) < budget_min:
                return False
        except (TypeError, ValueError):
            pass
    if "hourly_min" in filters:
        try:
            hourly_min = float(filters.get("hourly_min"))
            hourly_from = job.get("hourly_from")
            if hourly_from is None or float(hourly_from) < hourly_min:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _is_transient_error(exc: Exception) -> bool:
    if exc.__class__.__name__ in {"UpworkRateLimitError", "UpworkTransientError"}:
        return True
    text = str(exc).lower()
    return any(token in text for token in (" 429", "http 429", "http 500", "http 502", "http 503", "http 504"))


def _should_notify_auth_issue(state: _PollerState, user_id: int, now_mono: float) -> bool:
    uid = int(user_id)
    if uid not in state.last_auth_notify_at:
        state.last_auth_notify_at[uid] = now_mono
        return True
    last = state.last_auth_notify_at[uid]
    if now_mono - last < _AUTH_NOTIFY_COOLDOWN_SECONDS:
        return False
    state.last_auth_notify_at[uid] = now_mono
    return True


def _run_retention_cleanup(
    seen_retention_days: int,
    daily_counter_retention_days: int = _DAILY_COUNTER_RETENTION_DAYS,
) -> tuple[int, int]:
    now_utc = datetime.now(timezone.utc)
    seen_cutoff_iso = (now_utc - timedelta(days=max(1, int(seen_retention_days)))).isoformat()
    counters_cutoff_day = (
        now_utc - timedelta(days=max(1, int(daily_counter_retention_days)))
    ).strftime("%Y-%m-%d")
    removed_seen = db.cleanup_upwork_jobs_seen_before(seen_cutoff_iso)
    removed_counters = db.cleanup_upwork_daily_counters_before(counters_cutoff_day)
    return removed_seen, removed_counters


async def _send_alert(
    send_fn: SendFn,
    user_id: int,
    profile_id: int,
    text: str,
    *,
    reply_markup: Any | None = None,
) -> bool:
    try:
        await send_fn(user_id, text, reply_markup)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        text_lower = str(exc).lower()
        if "chat not found" in text_lower or "forbidden" in text_lower:
            return False
        retry_after_raw = getattr(exc, "retry_after", None)
        if retry_after_raw is not None:
            retry_after = int(retry_after_raw or 1)
            await asyncio.sleep(max(1, min(retry_after, 30)))
            try:
                await send_fn(user_id, text, reply_markup)
                return True
            except asyncio.CancelledError:
                raise
            except Exception as retry_exc:
                db.record_error(
                    "upwork_api",
                    retry_exc,
                    context={"user_id": user_id, "profile_id": profile_id, "action": "send_alert_retry"},
                )
                return False
        db.record_error(
            "upwork_api",
            exc,
            context={"user_id": user_id, "profile_id": profile_id, "action": "send_alert"},
        )
        return False


async def run_upwork_api_poll_cycle(
    send_fn: SendFn,
    *,
    state: _PollerState,
    semaphore: asyncio.Semaphore,
    search_fn: SearchFn | None = None,
) -> dict[str, int]:
    if search_fn is None:
        from app.integrations.upwork.client import search_public_jobs

        search_fn = search_public_jobs

    stats = {
        "profiles_processed": 0,
        "jobs_fetched": 0,
        "new_jobs": 0,
        "sent": 0,
        "skipped_not_connected": 0,
        "filtered_out_by_watermark": 0,
        "auth_errors": 0,
    }
    min_matched_skills = max(1, get_upwork_rss_min_matched_skills())
    profiles = db.upwork_profiles_list_enabled_all()
    digest_batches: dict[int, list[tuple[dict[str, Any], int, list[str]]]] = {}
    cycle_alert_counts: dict[int, int] = {}
    prefs_cache: dict[int, dict[str, object]] = {}
    skills_cache: dict[int, list[str]] = {}
    plan_cache: dict[int, str] = {}
    cycle_seen: set[tuple[int, str]] = set()
    now_mono = asyncio.get_running_loop().time()

    async def process_profile(profile: dict[str, Any]) -> None:
        profile_id = int(profile["id"])
        user_id = int(profile["user_id"])
        profile_key = (user_id, profile_id)
        if now_mono < state.next_allowed_by_profile.get(profile_key, 0.0):
            return

        account = db.upwork_oauth_get_account(user_id)
        if not account or int(account.get("is_connected") or 0) != 1:
            stats["skipped_not_connected"] += 1
            return

        query = str(profile.get("query") or "").strip()
        if not query:
            return

        async with semaphore:
            try:
                jobs = await search_fn(user_id, query, 20)
            except UpworkAuthError as exc:
                stats["auth_errors"] += 1
                revoked_at_iso = datetime.now(timezone.utc).isoformat()
                db.upwork_oauth_mark_disconnected(user_id, revoked_at_iso=revoked_at_iso)
                if _should_notify_auth_issue(state, user_id, now_mono):
                    await _send_alert(
                        send_fn,
                        user_id,
                        profile_id,
                        "⚠️ Upwork connection expired or was revoked. Please reconnect via /upwork_connect",
                        reply_markup=None,
                    )
                db.record_error(
                    "upwork_api",
                    exc,
                    context={"user_id": user_id, "profile_id": profile_id, "action": "auth_error_disconnect"},
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                fails = state.fail_counts_by_profile.get(profile_key, 0) + 1
                state.fail_counts_by_profile[profile_key] = fails
                if _is_transient_error(exc):
                    delay = _BACKOFF_BASE_SECONDS[min(fails - 1, len(_BACKOFF_BASE_SECONDS) - 1)]
                    delay += random.uniform(0.0, 3.0)
                    state.next_allowed_by_profile[profile_key] = now_mono + delay
                db.record_error(
                    "upwork_api",
                    exc,
                    context={"user_id": user_id, "profile_id": profile_id, "action": "search_public_jobs"},
                )
                return

        state.fail_counts_by_profile[profile_key] = 0
        state.next_allowed_by_profile[profile_key] = now_mono
        stats["profiles_processed"] += 1
        stats["jobs_fetched"] += len(jobs)
        last_published_at, last_job_id = _parse_profile_cursor(str(profile.get("last_cursor_json") or "") or None)
        jobs_filtered: list[dict[str, Any]] = []
        for job in jobs:
            if _job_is_newer_than_cursor(job, last_published_at, last_job_id):
                jobs_filtered.append(job)
            else:
                stats["filtered_out_by_watermark"] += 1

        for job in jobs_filtered:
            try:
                if not _filters_match(job, str(profile.get("filters_json") or "") or None):
                    continue
                job_uid = _build_job_uid(job)
                cycle_key = (user_id, job_uid)
                if cycle_key in cycle_seen:
                    continue
                if db.is_upwork_job_seen(user_id, job_uid):
                    cycle_seen.add(cycle_key)
                    continue

                title = str(job.get("title") or "")
                snippet = str(job.get("snippet") or "")
                search_text = " ".join([title, snippet])
                prefs = prefs_cache.get(user_id)
                if prefs is None:
                    prefs = db.get_upwork_user_prefs(user_id)
                    prefs_cache[user_id] = prefs
                mute_keywords = list(prefs.get("mute_keywords") or [])
                if _is_muted(search_text, mute_keywords):
                    cycle_seen.add(cycle_key)
                    continue

                skills = skills_cache.get(user_id)
                if skills is None:
                    skills = db.get_skills(user_id)
                    skills_cache[user_id] = skills
                if not skills:
                    db.mark_upwork_job_seen(user_id, job_uid)
                    cycle_seen.add(cycle_key)
                    continue

                score, matched = match_skills(search_text, skills)
                if len(matched) < min_matched_skills:
                    db.mark_upwork_job_seen(user_id, job_uid)
                    cycle_seen.add(cycle_key)
                    continue

                day_key = _day_key_now()
                plan = plan_cache.get(user_id)
                if plan is None:
                    plan = db.get_plan(user_id)
                    plan_cache[user_id] = plan
                cap = _daily_cap_for_plan(plan)
                sent_today = db.get_upwork_daily_alerts_sent(user_id, day_key)
                reserved = cycle_alert_counts.get(user_id, 0)
                if cap is not None and (sent_today + reserved) >= cap:
                    db.mark_upwork_job_seen(user_id, job_uid)
                    cycle_seen.add(cycle_key)
                    continue

                consumed = db.mark_upwork_job_seen(user_id, job_uid)
                cycle_seen.add(cycle_key)
                if not consumed:
                    continue
                stats["new_jobs"] += 1

                digest_mode = int(prefs.get("digest_mode") or 0) == 1
                if digest_mode:
                    digest_batches.setdefault(user_id, []).append((job, score, matched))
                    cycle_alert_counts[user_id] = reserved + 1
                    continue

                text, reply_markup = _item_alert_message(job, matched)
                sent = await _send_alert(send_fn, user_id, profile_id, text, reply_markup=reply_markup)
                if sent:
                    db.inc_upwork_daily_alerts_sent(user_id, day_key, delta=1)
                    cycle_alert_counts[user_id] = reserved + 1
                    stats["sent"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                db.record_error(
                    "upwork_api",
                    exc,
                    context={"user_id": user_id, "profile_id": profile_id, "action": "process_job"},
                )

        # Update per-profile watermark after a successful API call.
        best_dt, best_job_id = _max_watermark_from_jobs(jobs)
        if best_dt is not None:
            if _watermark_tuple(best_dt, best_job_id) > _watermark_tuple(last_published_at, last_job_id):
                cursor_json = json.dumps(
                    {
                        "last_published_at": best_dt.isoformat(),
                        "last_job_id": best_job_id,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                db.upwork_profiles_update_cursor(profile_id, cursor_json, datetime.now(timezone.utc).isoformat())

    if profiles:
        await asyncio.gather(*(process_profile(profile) for profile in profiles))

    for user_id, rows in digest_batches.items():
        if not rows:
            continue
        sent = await _send_alert(send_fn, user_id, 0, _digest_text(rows))
        if sent:
            db.inc_upwork_daily_alerts_sent(user_id, _day_key_now(), delta=len(rows))
            stats["sent"] += len(rows)

    return stats


async def run_upwork_api_poller(bot: Bot) -> None:
    interval_seconds = max(60, get_upwork_rss_poll_seconds())
    seen_retention_days = max(1, get_upwork_rss_seen_retention_days())
    state = _PollerState()
    state.next_cleanup_at = asyncio.get_running_loop().time() + _CLEANUP_INTERVAL_SECONDS
    semaphore = asyncio.Semaphore(_API_CONCURRENCY)
    loop = asyncio.get_running_loop()

    logger.info("Upwork API poller started (interval=%ss)", interval_seconds)

    async def _bot_send(user_id: int, text: str, reply_markup: Any | None) -> None:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )

    try:
        while True:
            try:
                stats = await run_upwork_api_poll_cycle(
                    send_fn=_bot_send,
                    state=state,
                    semaphore=semaphore,
                )
                logger.info(
                    "Upwork API cycle: profiles_processed=%s jobs_fetched=%s new_jobs=%s sent=%s skipped_not_connected=%s filtered_out_by_watermark=%s auth_errors=%s",
                    stats["profiles_processed"],
                    stats["jobs_fetched"],
                    stats["new_jobs"],
                    stats["sent"],
                    stats["skipped_not_connected"],
                    stats["filtered_out_by_watermark"],
                    stats["auth_errors"],
                )

                if loop.time() >= state.next_cleanup_at:
                    try:
                        _run_retention_cleanup(
                            seen_retention_days,
                            daily_counter_retention_days=_DAILY_COUNTER_RETENTION_DAYS,
                        )
                    except Exception as cleanup_exc:
                        db.record_error("upwork_api", cleanup_exc, context={"action": "retention_cleanup"})
                    finally:
                        state.next_cleanup_at = loop.time() + _CLEANUP_INTERVAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                db.record_error("upwork_api", exc, context={"action": "poll_cycle"})
                logger.warning("Upwork API poll cycle failed", exc_info=True)

            jitter = random.randint(0, max(5, interval_seconds // 8))
            await asyncio.sleep(interval_seconds + jitter)
    except asyncio.CancelledError:
        logger.info("Upwork API poller stopped")
        raise

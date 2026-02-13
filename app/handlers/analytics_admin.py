"""Admin analytics commands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message

from app.analytics import day_window_utc, rolling_days_window_utc
from app.analytics.retention import (
    get_dau_series,
    get_mau_series_28d,
    get_pro_health,
    get_retention_7d_series,
    get_wau_series,
)
from app.config import is_admin
from app.db import (
    get_block_reasons,
    get_event_counts,
    get_filtered_metrics,
    get_funnel,
    get_lead_quality_metrics,
    get_level_distribution,
    get_score_buckets,
    get_source_stats,
)

router = Router()


def _is_admin(message: Message) -> bool:
    user = message.from_user
    return bool(user and is_admin(user.id))


def _pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.0%"
    return f"{(100.0 * numerator / denominator):.1f}%"


def _parse_limit(command: CommandObject, default: int = 10) -> int:
    raw = (command.args or "").strip()
    if not raw:
        return default
    parts = raw.split()
    if len(parts) != 1 or not parts[0].isdigit():
        raise ValueError
    limit = int(parts[0])
    if limit < 1 or limit > 50:
        raise ValueError
    return limit


def _series_window_utc(days: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    since = today_start - timedelta(days=max(1, int(days)) - 1)
    until = today_start + timedelta(days=1)
    return since.isoformat(), until.isoformat()


def _fmt_ratio(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.0%"
    return f"{(100.0 * numerator / denominator):.1f}%"


def _render_retention_lines(days: int) -> list[str]:
    since_iso, until_iso = _series_window_utc(days)
    dau_rows = get_dau_series(since_iso, until_iso)
    wau_rows = get_wau_series(since_iso, until_iso)
    mau_rows = get_mau_series_28d(since_iso, until_iso)
    retention_rows = get_retention_7d_series(since_iso, until_iso)

    by_day: dict[str, dict[str, float | int]] = {}
    for row in dau_rows:
        by_day.setdefault(str(row["date"]), {})
        by_day[str(row["date"])]["dau"] = int(row.get("dau", 0))
    for row in wau_rows:
        by_day.setdefault(str(row["date"]), {})
        by_day[str(row["date"])]["wau"] = int(row.get("wau", 0))
    for row in mau_rows:
        by_day.setdefault(str(row["date"]), {})
        by_day[str(row["date"])]["mau"] = int(row.get("mau", 0))
    for row in retention_rows:
        by_day.setdefault(str(row["date"]), {})
        by_day[str(row["date"])]["retention_7d_pct"] = float(row.get("retention_7d_pct", 0.0))

    rows: list[str] = []
    rows.append("date       dau  wau  mau  dau/wau dau/mau  ret7d")
    for day in sorted(by_day):
        dau = int(by_day[day].get("dau", 0))
        wau = int(by_day[day].get("wau", 0))
        mau = int(by_day[day].get("mau", 0))
        retention_pct = float(by_day[day].get("retention_7d_pct", 0.0))
        rows.append(
            f"{day} {dau:>4} {wau:>4} {mau:>4} "
            f"{_fmt_ratio(dau, wau):>7} {_fmt_ratio(dau, mau):>7} {retention_pct:>6.1f}%"
        )
    return rows


def _render_pro_health_30d_lines() -> list[str]:
    since_iso, until_iso = rolling_days_window_utc(30)
    health = get_pro_health(since_iso, until_iso)
    conversions = health.get("conversions", {})
    time_to_activate = health.get("time_to_activate", {})
    avg_minutes = time_to_activate.get("avg_minutes")
    median_minutes = time_to_activate.get("median_minutes")
    tta_line = "time_to_activate: -"
    if avg_minutes is not None:
        if median_minutes is not None:
            tta_line = f"time_to_activate: median {float(median_minutes):.1f}m, avg {float(avg_minutes):.1f}m"
        else:
            tta_line = f"time_to_activate: avg {float(avg_minutes):.1f}m"

    lines = [
        "PRO health (last 30d UTC):",
        f"pro_activated: {int(health.get('pro_activated', 0))}",
        f"pro_downgraded: {int(health.get('pro_downgraded', 0))}",
        f"subscription_canceled: {int(health.get('subscription_canceled', 0))}",
        f"subscription_deleted: {int(health.get('subscription_deleted', 0))}",
        f"net_change: {int(health.get('net_change', 0))}",
        f"active_pro_now: {int(health.get('active_pro_now', 0))}",
        "conversions:",
        f"checkout->paid: {float(conversions.get('checkout_to_paid_pct', 0.0)):.1f}%",
        f"paid->activated: {float(conversions.get('paid_to_activated_pct', 0.0)):.1f}%",
        f"upgrade_requested->activated: {float(conversions.get('upgrade_requested_to_activated_pct', 0.0)):.1f}%",
        tta_line,
    ]
    return lines


@router.message(Command("stats_today"))
async def handle_stats_today(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    since_iso, until_iso = day_window_utc()
    counts = get_event_counts(None, since_iso, until_iso)

    lines = [
        "Analytics (today UTC):",
        f"lead_ingested: {counts.get('lead_ingested', 0)}",
        f"lead_matched: {counts.get('lead_matched', 0)}",
        f"lead_sent: {counts.get('lead_sent', 0)}",
        f"upgrade_requested: {counts.get('upgrade_requested', 0)}",
        f"checkout_created: {counts.get('checkout_created', 0)}",
        f"payment_confirmed: {counts.get('payment_confirmed', 0)}",
        f"pro_activated: {counts.get('pro_activated', 0)}",
        f"pro_downgraded: {counts.get('pro_downgraded', 0)}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("stats_7d"))
async def handle_stats_7d(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    since_iso, until_iso = rolling_days_window_utc(7)
    counts = get_event_counts(None, since_iso, until_iso)

    lines = [
        "Analytics (last 7d UTC):",
        f"lead_ingested: {counts.get('lead_ingested', 0)}",
        f"lead_matched: {counts.get('lead_matched', 0)}",
        f"lead_sent: {counts.get('lead_sent', 0)}",
        f"upgrade_requested: {counts.get('upgrade_requested', 0)}",
        f"checkout_created: {counts.get('checkout_created', 0)}",
        f"payment_confirmed: {counts.get('payment_confirmed', 0)}",
        f"pro_activated: {counts.get('pro_activated', 0)}",
        f"pro_downgraded: {counts.get('pro_downgraded', 0)}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("funnel_7d"))
async def handle_funnel_7d(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    since_iso, until_iso = rolling_days_window_utc(7)
    funnel = get_funnel(since_iso, until_iso)

    checkout_created = int(funnel.get("checkout_created", 0))
    payment_confirmed = int(funnel.get("payment_confirmed", 0))
    pro_activated = int(funnel.get("pro_activated", 0))
    upgrade_requested = int(funnel.get("upgrade_requested", 0))

    lines = [
        "Funnel (last 7d UTC):",
        f"leads_ingested: {funnel.get('leads_ingested', 0)}",
        f"leads_matched: {funnel.get('leads_matched', 0)}",
        f"leads_sent: {funnel.get('leads_sent', 0)}",
        f"upgrade_requested: {upgrade_requested}",
        f"checkout_created: {checkout_created}",
        f"payment_confirmed: {payment_confirmed}",
        f"pro_activated: {pro_activated}",
        "Conversions:",
        f"checkout->payment: {_pct(payment_confirmed, checkout_created)}",
        f"payment->activation: {_pct(pro_activated, payment_confirmed)}",
        f"upgrade_requested->activation: {_pct(pro_activated, upgrade_requested)}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("lead_quality_7d"))
async def handle_lead_quality_7d(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    since_iso, until_iso = rolling_days_window_utc(7)
    metrics = get_lead_quality_metrics(since_iso, until_iso)
    dist = metrics.get("match_level_distribution", {})
    avg_score = metrics.get("avg_score")

    lines = [
        "Lead quality (last 7d UTC):",
        f"LOW: {dist.get('LOW', 0)}",
        f"MEDIUM: {dist.get('MEDIUM', 0)}",
        f"HIGH: {dist.get('HIGH', 0)}",
        f"Avg score: {avg_score:.1f}" if isinstance(avg_score, float) else "Avg score: -",
        f"Filtered: {metrics.get('filtered_count', 0)} / {metrics.get('ingested_count', 0)} ({metrics.get('filtered_pct', 0.0):.1f}%)",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("quality_7d"))
async def handle_quality_7d(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    since_iso, until_iso = rolling_days_window_utc(7)
    score_buckets = get_score_buckets(since_iso, until_iso)
    levels = get_level_distribution(since_iso, until_iso)
    filtered = get_filtered_metrics(since_iso, until_iso)
    quality = get_lead_quality_metrics(since_iso, until_iso)
    counts = get_event_counts(None, since_iso, until_iso)
    matched_count = int(counts.get("lead_matched", 0))
    avg_score = quality.get("avg_score")

    lines = [
        "QUALITY (7d UTC)",
        f"ingested: {filtered['ingested']}",
        f"filtered: {filtered['filtered']} ({filtered['filtered_rate'] * 100:.1f}%)",
        f"matched: {matched_count}",
        f"avg score: {avg_score:.1f}" if avg_score is not None else "avg score: -",
        "score buckets:",
        f"0-24: {score_buckets['0-24']}",
        f"25-49: {score_buckets['25-49']}",
        f"50-69: {score_buckets['50-69']}",
        f"70-84: {score_buckets['70-84']}",
        f"85-100: {score_buckets['85-100']}",
        f"101+: {score_buckets['101+']}",
        "match levels:",
        f"LOW: {levels.get('LOW', 0)}",
        f"MEDIUM: {levels.get('MEDIUM', 0)}",
        f"HIGH: {levels.get('HIGH', 0)}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("blocks_7d"))
async def handle_blocks_7d(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    since_iso, until_iso = rolling_days_window_utc(7)
    blocks = get_block_reasons(since_iso, until_iso)
    reasons = blocks.get("reasons", {})
    by_plan = blocks.get("by_plan", {})

    lines = [
        "BLOCKS (7d UTC)",
        f"dedupe: {reasons.get('dedupe', 0)}",
        f"cooldown: {reasons.get('cooldown', 0)}",
        f"cap: {reasons.get('cap', 0)}",
        f"free_limit: {reasons.get('free_limit', 0)}",
        "by plan:",
        f"FREE: {sum(int(v) for v in by_plan.get('FREE', {}).values())}",
        f"PRO: {sum(int(v) for v in by_plan.get('PRO', {}).values())}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("sources_7d"))
async def handle_sources_7d(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    try:
        limit = _parse_limit(command, default=10)
    except ValueError:
        await message.answer("Usage: /sources_7d [limit]\nExample: /sources_7d 15")
        return

    since_iso, until_iso = rolling_days_window_utc(7)
    rows = get_source_stats(since_iso, until_iso, limit=limit)
    if not rows:
        await message.answer("SOURCES (7d UTC)\nNo source data.")
        return

    lines = ["SOURCES (7d UTC)"]
    for row in rows:
        lines.append(
            f"{row['source']}: ingested {row['ingested']}, matched {row['matched']}, sent {row['sent']}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("retention_7d"))
async def handle_retention_7d(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    lines = ["Retention (last 7d UTC):"]
    lines.extend(_render_retention_lines(7))
    await message.answer("\n".join(lines))


@router.message(Command("retention_30d"))
async def handle_retention_30d(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    rows = _render_retention_lines(30)
    if len(rows) <= 9:
        lines = ["Retention (last 30d UTC):"]
        lines.extend(rows)
        await message.answer("\n".join(lines))
        return

    data_rows = rows[1:]
    dau_total = 0
    wau_total = 0
    mau_total = 0
    retention_total = 0.0
    parsed_rows = 0
    for row in data_rows:
        parts = row.split()
        if len(parts) < 7:
            continue
        try:
            dau_total += int(parts[1])
            wau_total += int(parts[2])
            mau_total += int(parts[3])
            retention_total += float(parts[6].replace("%", ""))
            parsed_rows += 1
        except Exception:
            continue

    avg_retention = (retention_total / parsed_rows) if parsed_rows > 0 else 0.0
    lines = [
        "Retention (last 30d UTC):",
        f"summary: days={parsed_rows}, avg DAU={int(dau_total / parsed_rows) if parsed_rows else 0}, avg WAU={int(wau_total / parsed_rows) if parsed_rows else 0}, avg MAU={int(mau_total / parsed_rows) if parsed_rows else 0}, avg ret7d={avg_retention:.1f}%",
        "last 7 days:",
        rows[0],
    ]
    lines.extend(data_rows[-7:])
    await message.answer("\n".join(lines))


@router.message(Command("pro_health_30d"))
async def handle_pro_health_30d(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("Unauthorized")
        return

    await message.answer("\n".join(_render_pro_health_30d_lines()))

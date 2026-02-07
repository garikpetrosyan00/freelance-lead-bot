"""Monetization gating rules."""

from __future__ import annotations

FREE_DAILY_CAP = 5


def allowed_match_levels(plan: str) -> set[str]:
    plan = plan.upper().strip()
    if plan == "PRO":
        return {"LOW", "MEDIUM", "HIGH"}
    return {"MEDIUM", "HIGH"}


def level_rank(level: str) -> int:
    level = level.upper().strip()
    return {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(level, 0)


def effective_cap(plan: str, user_cap: int | None) -> int | None:
    plan = plan.upper().strip()
    if plan == "FREE":
        return FREE_DAILY_CAP
    return user_cap


def passes_min_level(match_level: str, min_level: str) -> bool:
    return level_rank(match_level) >= level_rank(min_level)


def can_send_notification(
    plan: str,
    match_level: str,
    sent_today: int,
    min_level: str,
    cap: int | None,
) -> tuple[bool, str]:
    allowed_levels = allowed_match_levels(plan)
    if match_level not in allowed_levels:
        return False, "level_not_allowed"

    if not passes_min_level(match_level, min_level):
        return False, "below_min_level"

    if cap is not None and sent_today >= cap:
        return False, "daily_cap_reached"

    return True, "ok"

"""Monetization gating rules."""

from __future__ import annotations

FREE_DAILY_CAP = 5


def allowed_match_levels(plan: str) -> set[str]:
    plan = plan.upper().strip()
    if plan == "PRO":
        return {"LOW", "MEDIUM", "HIGH"}
    return {"MEDIUM", "HIGH"}


def can_send_notification(plan: str, match_level: str, sent_today: int) -> tuple[bool, str]:
    allowed_levels = allowed_match_levels(plan)
    if match_level not in allowed_levels:
        return False, "level_not_allowed"

    plan = plan.upper().strip()
    if plan == "FREE" and sent_today >= FREE_DAILY_CAP:
        return False, "daily_cap_reached"

    return True, "ok"

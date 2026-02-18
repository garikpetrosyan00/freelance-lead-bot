"""Monetization gating rules."""

from __future__ import annotations

FREE_DAILY_CAP = 3


def effective_cap(plan: str, user_cap: int | None) -> int | None:
    _ = user_cap  # daily cap is fixed by plan in the simplified model
    plan = plan.upper().strip()
    cap = None if plan == "PRO" else FREE_DAILY_CAP
    if plan == "FREE":
        assert cap == FREE_DAILY_CAP == 3
    if plan == "PRO":
        assert cap is None
    return cap


def passes_min_skill_matches(overlap_count: int, min_skill_matches: int) -> bool:
    return int(overlap_count) >= max(1, int(min_skill_matches))


def can_send_notification(
    plan: str,
    overlap_count: int,
    sent_today: int,
    min_skill_matches: int,
    cap: int | None,
) -> tuple[bool, str]:
    _ = plan
    if not passes_min_skill_matches(overlap_count, min_skill_matches):
        return False, "below_min_skill_matches"

    if cap is not None and sent_today >= cap:
        return False, "daily_cap_reached"

    return True, "ok"

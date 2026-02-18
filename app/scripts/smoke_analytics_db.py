"""Smoke test for analytics_events DB helpers."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone, timedelta

from app import db
from app.analytics.retention import (
    get_dau_series,
    get_mau_series_28d,
    get_pro_health,
    get_retention_7d_series,
    get_wau_series,
)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "analytics_smoke.db")
        db.configure_db(path)
        db.init_db()

        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=1)).isoformat()
        until = (now + timedelta(days=1)).isoformat()

        db.log_event("lead_ingested", lead_id="lead1")
        db.log_event("lead_ingested", lead_id="lead2", meta={"source": "telegram"})
        db.log_event("lead_filtered", lead_id="lead2", meta={"reason": "too_short", "source": "telegram"})
        db.log_event("lead_matched", user_id=1, lead_id="lead1", match_level="HIGH", score=88, plan="FREE")
        db.log_event("lead_matched", user_id=2, lead_id="lead2", match_level="LOW", score=23, plan="FREE", meta={"source": "telegram"})
        db.log_event("lead_sent", user_id=1, lead_id="lead1", match_level="HIGH", score=88, plan="FREE")
        db.log_event("lead_blocked", user_id=2, lead_id="lead1", plan="FREE", meta={"reason": "cap"})
        db.log_event("lead_blocked", user_id=3, lead_id="lead2", plan="PRO", meta={"reason": "cooldown"})
        db.log_event("checkout_created", user_id=1, plan="FREE")
        db.log_event("payment_confirmed", user_id=1, plan="PRO")
        db.log_event("pro_activated", user_id=1, plan="PRO")
        db.log_event(
            "checkout_completed",
            user_id=5,
            meta={"checkout_session_id": "cs_smoke_123"},
        )
        db.log_event(
            "reconcile_probe",
            user_id=6,
            ts=(now - timedelta(minutes=2)).isoformat(),
        )

        # Multi-day retention/pro-health fixtures.
        db.log_event("lead_sent", user_id=1, ts="2026-02-01T09:00:00+00:00")
        db.log_event("checkout_created", user_id=2, ts="2026-02-01T11:00:00+00:00")
        db.log_event("lead_blocked", user_id=1, ts="2026-02-02T09:00:00+00:00", meta={"reason": "cap"})
        db.log_event("checkout_created", user_id=1, ts="2026-02-08T10:00:00+00:00")
        db.log_event("payment_confirmed", user_id=1, ts="2026-02-08T10:20:00+00:00")
        db.log_event("pro_activated", user_id=1, ts="2026-02-08T10:30:00+00:00", plan="PRO")
        db.log_event("upgrade_requested", user_id=3, ts="2026-02-08T11:00:00+00:00")
        db.log_event("lead_sent", user_id=2, ts="2026-02-09T10:00:00+00:00")
        db.log_event("pro_downgraded", user_id=4, ts="2026-02-10T12:00:00+00:00", plan="FREE")
        db.mark_user_pro(user_id=1, enabled=True, activated_at_iso="2026-02-08T10:30:00+00:00", plan="PRO")
        db.mark_user_pro(user_id=4, enabled=False, activated_at_iso="2026-02-10T12:00:00+00:00", plan="FREE")

        counts = db.get_event_counts(None, since, until)
        assert counts.get("lead_ingested", 0) >= 2
        assert counts.get("pro_activated", 0) == 1

        funnel = db.get_funnel(since, until)
        assert funnel["leads_ingested"] >= 2
        assert funnel["payment_confirmed"] == 1

        quality = db.get_lead_quality_metrics(since, until)
        assert quality["match_level_distribution"]["HIGH"] == 1
        assert quality["avg_score"] is not None

        buckets = db.get_score_buckets(since, until)
        assert buckets["0-24"] >= 1
        assert buckets["85-100"] >= 1

        levels = db.get_level_distribution(since, until)
        assert levels["LOW"] >= 1
        assert levels["HIGH"] >= 1

        filtered = db.get_filtered_metrics(since, until)
        assert filtered["ingested"] >= 2
        assert filtered["filtered"] >= 1

        blocks = db.get_block_reasons(since, until)
        assert blocks["reasons"].get("cap", 0) >= 1
        assert blocks["reasons"].get("cooldown", 0) >= 1
        assert db.has_event_with_session(5, "checkout_completed", "cs_smoke_123") is True
        assert db.has_event_with_session(5, "checkout_completed", "cs_other") is False
        assert db.has_recent_event(6, "reconcile_probe", within_minutes=5) is True
        assert db.has_recent_event(6, "reconcile_probe", within_minutes=1) is False

        sources = db.get_source_stats(since, until, limit=10)
        assert isinstance(sources, list)

        since_ret = "2026-02-01T00:00:00+00:00"
        until_ret = "2026-02-11T00:00:00+00:00"
        dau = get_dau_series(since_ret, until_ret)
        wau = get_wau_series(since_ret, until_ret)
        mau = get_mau_series_28d(since_ret, until_ret)
        retention = get_retention_7d_series(since_ret, until_ret)
        pro_health = get_pro_health(since_ret, until_ret)

        assert dau, "DAU series should not be empty"
        assert wau, "WAU series should not be empty"
        assert mau, "MAU series should not be empty"
        assert retention, "retention series should not be empty"

        dau_by_day = {str(row["date"]): int(row["dau"]) for row in dau}
        retention_by_day = {str(row["date"]): row for row in retention}
        assert dau_by_day.get("2026-02-01", 0) >= 2
        assert dau_by_day.get("2026-02-08", 0) >= 1
        assert float(retention_by_day["2026-02-08"]["retention_7d_pct"]) > 0.0
        assert int(pro_health["pro_activated"]) >= 1
        assert int(pro_health["pro_downgraded"]) >= 1
        assert int(pro_health["active_pro_now"]) >= 1

        # Isolated active_pro_now consistency case:
        # user_id=100 gets activated then downgraded -> current active should be 0.
        isolated_path = os.path.join(tmp, "analytics_smoke_isolated.db")
        db.configure_db(isolated_path)
        db.init_db()
        db.activate_pro_for_user(
            user_id=100,
            reason="smoke_activate",
            activated_at_iso="2026-02-01T10:00:00+00:00",
        )
        db.downgrade_user_to_free(
            user_id=100,
            reason="smoke_downgrade",
            updated_at_iso="2026-02-01T11:00:00+00:00",
        )
        isolated_health = get_pro_health(
            "2026-02-01T00:00:00+00:00",
            "2026-02-02T00:00:00+00:00",
        )
        assert int(isolated_health["active_pro_now"]) == 0

    print("smoke_analytics_db: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

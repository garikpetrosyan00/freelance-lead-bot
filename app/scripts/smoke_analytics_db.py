"""Smoke test for analytics_events DB helpers."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone, timedelta

from app import db


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

        sources = db.get_source_stats(since, until, limit=10)
        assert isinstance(sources, list)

    print("smoke_analytics_db: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Smoke test for monitoring health checks and alert persistence."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

from app import db
from app.monitoring.health import (
    clear_silence,
    record_alert,
    run_health_checks,
    set_silence,
    should_send_alert,
)


def _iso_ago(*, hours: int = 0, minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours, minutes=minutes)).isoformat()


def main() -> int:
    old_secret = os.environ.get("STRIPE_SECRET")
    old_secret_legacy = os.environ.get("STRIPE_SECRET_KEY")
    old_webhook = os.environ.get("STRIPE_WEBHOOK_SECRET")
    old_db_path = db.DB_PATH
    old_db_uri = db.DB_URI
    os.environ["STRIPE_SECRET"] = "sk_test_smoke"
    os.environ.pop("STRIPE_SECRET_KEY", None)
    os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_smoke"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "monitor_smoke.db")
            db.configure_db(path)
            db.init_db()

            # Ingestion stalled (>120m) and delivery stalled (>60m with traffic).
            db.log_event("lead_ingested", user_id=1, ts=_iso_ago(hours=3))
            db.log_event("lead_matched", user_id=1, ts=_iso_ago(minutes=20))

            # High blocked ratio in last 24h.
            for _ in range(9):
                db.log_event("lead_blocked", user_id=2, ts=_iso_ago(minutes=15), meta={"reason": "cap"})
            db.log_event("lead_sent", user_id=2, ts=_iso_ago(hours=2))

            # Checkout traffic with no payment in last 6h.
            db.log_event("checkout_created", user_id=3, ts=_iso_ago(minutes=30))

            alerts = run_health_checks()
            assert alerts, "expected monitoring alerts"
            alert_types = {str(a.get("type")) for a in alerts}

            assert "ingestion_stalled" in alert_types
            assert "delivery_stalled" in alert_types
            assert "delivery_blocked_ratio_high" in alert_types
            assert "webhook_payment_gap" in alert_types

            # Case A: checkout>0, payment>0, processed_last stale -> NO CRITICAL webhook_stalled.
            with db._connect() as conn:
                conn.execute("DELETE FROM analytics_events")
                conn.execute("DELETE FROM processed_events")
                conn.execute(
                    "INSERT INTO processed_events(provider, event_id, created_at) VALUES (?, ?, ?)",
                    ("stripe", "evt_old_a", _iso_ago(hours=8)),
                )
                conn.commit()
            db.log_event("checkout_created", user_id=10, ts=_iso_ago(minutes=20))
            db.log_event("payment_confirmed", user_id=10, ts=_iso_ago(minutes=10))
            alerts_case_a = run_health_checks()
            alert_types_a = {str(a.get("type")) for a in alerts_case_a}
            assert "webhook_stalled" not in alert_types_a

            # Case B: checkout>0, payment=0, processed_last stale -> CRITICAL webhook_stalled.
            with db._connect() as conn:
                conn.execute("DELETE FROM analytics_events")
                conn.execute("DELETE FROM processed_events")
                conn.execute(
                    "INSERT INTO processed_events(provider, event_id, created_at) VALUES (?, ?, ?)",
                    ("stripe", "evt_old_b", _iso_ago(hours=8)),
                )
                conn.commit()
            db.log_event("checkout_created", user_id=11, ts=_iso_ago(minutes=20))
            alerts_case_b = run_health_checks()
            alert_types_b = {str(a.get("type")) for a in alerts_case_b}
            assert "webhook_stalled" in alert_types_b

            test_alert = {
                "type": "smoke_alert",
                "severity": "WARN",
                "message": "smoke",
                "meta": {},
            }
            assert should_send_alert("smoke_alert", cooldown_minutes=60) is True
            record_alert(test_alert)
            assert should_send_alert("smoke_alert", cooldown_minutes=60) is False

            set_silence("smoke_silenced", 5)
            assert should_send_alert("smoke_silenced", cooldown_minutes=0) is False
            clear_silence("smoke_silenced")
    finally:
        db.configure_db(old_db_path, uri=old_db_uri)
        if old_secret is None:
            os.environ.pop("STRIPE_SECRET", None)
        else:
            os.environ["STRIPE_SECRET"] = old_secret
        if old_secret_legacy is None:
            os.environ.pop("STRIPE_SECRET_KEY", None)
        else:
            os.environ["STRIPE_SECRET_KEY"] = old_secret_legacy
        if old_webhook is None:
            os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        else:
            os.environ["STRIPE_WEBHOOK_SECRET"] = old_webhook

    print("smoke_monitoring: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

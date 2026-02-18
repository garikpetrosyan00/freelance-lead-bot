"""Smoke test for analytics_events DB helpers."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import types
from datetime import datetime, timezone, timedelta

from app import db
from app.analytics.retention import (
    get_dau_series,
    get_mau_series_28d,
    get_pro_health,
    get_retention_7d_series,
    get_wau_series,
)
from app.gating import can_send_notification, effective_cap
from app.leads import Lead
from app.matching import match_lead
from app.monetization.teaser import _teaser_text
if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

from app.webhooks.stripe_webhook import _process_stripe_event


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "analytics_smoke.db")
        db.configure_db(path)
        db.init_db()

        # JSON1 support detection should be cached after first evaluation.
        original_connect = db._connect
        original_json_support = db._JSON_EXTRACT_SUPPORTED
        connect_calls = [0]

        def _counting_connect():
            connect_calls[0] += 1
            return original_connect()

        db._JSON_EXTRACT_SUPPORTED = None
        db._connect = _counting_connect
        try:
            first_json1 = db._json_extract_supported()
            second_json1 = db._json_extract_supported()
            assert first_json1 == second_json1
            assert connect_calls[0] == 1
        finally:
            db._connect = original_connect
            db._JSON_EXTRACT_SUPPORTED = original_json_support

        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=1)).isoformat()
        until = (now + timedelta(days=1)).isoformat()

        db.set_skills(42, ["python", "django", "react", "postgresql", "docker"])
        default_min_skill_matches, _ = db.get_user_settings(42)
        assert int(default_min_skill_matches) == 3
        db.set_user_min_skill_matches(42, 5)
        updated_min_skill_matches, _ = db.get_user_settings(42)
        assert int(updated_min_skill_matches) == 5

        # DB layer still clamps as last line of defense.
        db.set_skills(43, ["python", "react", "sql"])
        db.set_user_min_skill_matches(43, 3)
        before_min_skill_matches, _ = db.get_user_settings(43)
        assert int(before_min_skill_matches) == 3
        min_allowed_43, max_allowed_43 = db.min_skill_matches_bounds_for_user(43)
        assert (min_allowed_43, max_allowed_43) == (3, 3)
        assert db.min_skill_matches_range_message(3) == (
            "You selected 3 skills, so the maximum is 3. "
            "Minimum is 3 when you have 3+ skills selected. "
            "Choose 3-3."
        )

        # User-facing behavior should reject out-of-range values and keep state unchanged.
        reject_high_message = db.min_skill_matches_validation_error(5, len(db.get_skills(43)))
        assert reject_high_message == db.min_skill_matches_range_message(3)
        after_reject_high, _ = db.get_user_settings(43)
        assert int(after_reject_high) == 3
        reject_low_message = db.min_skill_matches_validation_error(2, len(db.get_skills(43)))
        assert reject_low_message == db.min_skill_matches_range_message(3)
        after_reject_low, _ = db.get_user_settings(43)
        assert int(after_reject_low) == 3

        db.set_user_min_skill_matches(43, 5)
        clamped_min_skill_matches, _ = db.get_user_settings(43)
        assert int(clamped_min_skill_matches) == 3

        db.set_skills(44, ["python", "sql"])
        min_allowed_44, max_allowed_44 = db.min_skill_matches_bounds_for_user(44)
        assert (min_allowed_44, max_allowed_44) == (1, 2)
        assert db.min_skill_matches_validation_error(2, len(db.get_skills(44))) is None
        db.set_user_min_skill_matches(44, 2)
        updated_min_skill_matches_44, _ = db.get_user_settings(44)
        assert int(updated_min_skill_matches_44) == 2

        class _BotStub:
            def __init__(self) -> None:
                self.messages: list[tuple[int, str]] = []

            async def send_message(self, chat_id: int, text: str) -> None:
                self.messages.append((int(chat_id), str(text)))

        def _count_pro_activated(user_id: int) -> int:
            with sqlite3.connect(path, uri=False) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM analytics_events WHERE event = 'pro_activated' AND user_id = ?",
                    (user_id,),
                ).fetchone()
            return int(row[0]) if row else 0

        async def _run_webhook_smoke() -> None:
            bot = _BotStub()

            # A) checkout.session.completed unpaid -> no activation.
            user_unpaid = 60
            unpaid_event = {
                "id": "evt_smoke_checkout_unpaid",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_smoke_unpaid",
                        "payment_status": "unpaid",
                        "metadata": {"user_id": str(user_unpaid), "telegram_user_id": str(user_unpaid)},
                        "subscription": "",
                        "customer": "",
                        "currency": "usd",
                    }
                },
            }
            before_unpaid = _count_pro_activated(user_unpaid)
            result_unpaid = await _process_stripe_event(bot, unpaid_event)
            assert result_unpaid.get("ok") is True
            assert db.get_plan(user_unpaid) == "FREE"
            assert _count_pro_activated(user_unpaid) == before_unpaid

            # B) checkout.session.completed paid -> activation.
            user_paid = 61
            paid_event = {
                "id": "evt_smoke_checkout_paid",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_smoke_paid",
                        "payment_status": "paid",
                        "metadata": {"user_id": str(user_paid), "telegram_user_id": str(user_paid)},
                        "subscription": "sub_smoke_paid",
                        "customer": "cus_smoke_paid",
                        "currency": "usd",
                    }
                },
            }
            before_paid = _count_pro_activated(user_paid)
            result_paid = await _process_stripe_event(bot, paid_event)
            assert result_paid.get("ok") is True
            assert db.get_plan(user_paid) == "PRO"
            assert _count_pro_activated(user_paid) == before_paid + 1

            # C) invoice.payment_succeeded paid with already-PRO user -> no duplicate activation.
            user_invoice = 62
            db.mark_user_pro(user_invoice, enabled=True, activated_at_iso=now.isoformat(), plan="PRO")
            db.upsert_stripe_subscription(
                user_id=user_invoice,
                subscription_id="sub_smoke_invoice_paid",
                customer_id="cus_smoke_invoice_paid",
                status="active",
                current_period_end=None,
            )
            before_invoice = _count_pro_activated(user_invoice)
            invoice_event = {
                "id": "evt_smoke_invoice_paid",
                "type": "invoice.payment_succeeded",
                "data": {
                    "object": {
                        "status": "paid",
                        "subscription": "sub_smoke_invoice_paid",
                        "amount_paid": 1000,
                        "currency": "usd",
                        "customer": "cus_smoke_invoice_paid",
                    }
                },
            }
            result_invoice = await _process_stripe_event(bot, invoice_event)
            assert result_invoice.get("ok") is True
            assert db.get_plan(user_invoice) == "PRO"
            assert _count_pro_activated(user_invoice) == before_invoice

            # D) duplicate event id -> activation only once.
            user_dup = 63
            dup_event = {
                "id": "evt_smoke_duplicate_checkout",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_smoke_duplicate",
                        "payment_status": "paid",
                        "metadata": {"user_id": str(user_dup), "telegram_user_id": str(user_dup)},
                        "subscription": "sub_smoke_duplicate",
                        "customer": "cus_smoke_duplicate",
                        "currency": "usd",
                    }
                },
            }
            before_dup = _count_pro_activated(user_dup)
            first = await _process_stripe_event(bot, dup_event)
            second = await _process_stripe_event(bot, dup_event)
            assert first.get("ok") is True
            assert second.get("duplicate") is True
            assert db.get_plan(user_dup) == "PRO"
            assert _count_pro_activated(user_dup) == before_dup + 1

        asyncio.run(_run_webhook_smoke())

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
        # Compact JSON via log_event serializer.
        db.log_event(
            "checkout_completed",
            user_id=5,
            meta={"checkout_session_id": "cs_smoke_123"},
        )
        # Spaced JSON inserted directly to ensure formatting variance is handled.
        with sqlite3.connect(path, uri=False) as conn:
            conn.execute(
                """
                INSERT INTO analytics_events (ts, event, user_id, meta_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    now.isoformat(),
                    "checkout_completed",
                    5,
                    '{"checkout_session_id": "cs_smoke_spaced_456"}',
                ),
            )
            conn.commit()
        special_session_id = r"abc%_\123"
        db.log_event(
            "checkout_completed",
            user_id=5,
            meta={"checkout_session_id": special_session_id},
        )

        recent_now_iso = now.isoformat()
        recent_old_iso = (now - timedelta(minutes=2)).isoformat()
        db.log_event("recent_probe_now", user_id=6, ts=recent_now_iso)
        db.log_event("recent_probe_old", user_id=6, ts=recent_old_iso)

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
        assert counts.get("pro_activated", 0) >= 1

        funnel = db.get_funnel(since, until)
        assert funnel["leads_ingested"] >= 2
        assert funnel["payment_confirmed"] >= 1

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
        assert db.has_event_with_session(5, "checkout_completed", "cs_smoke_spaced_456") is True
        assert db.has_event_with_session(5, "checkout_completed", "cs_other") is False
        prior_json_support = db._JSON_EXTRACT_SUPPORTED
        db._JSON_EXTRACT_SUPPORTED = False
        try:
            assert db.has_event_with_session(5, "checkout_completed", special_session_id) is True
        finally:
            db._JSON_EXTRACT_SUPPORTED = prior_json_support
        assert db.has_recent_event(6, "recent_probe_now", within_minutes=1) is True
        assert db.has_recent_event(6, "recent_probe_old", within_minutes=1) is False
        assert db.has_recent_event(6, "recent_probe_old", within_minutes=3) is True
        assert db.has_recent_event(6, "recent_probe_old", within_minutes=0) is False

        # Stored timestamps are ISO-8601 UTC strings with offsets, so lexical compare works.
        with sqlite3.connect(path, uri=False) as conn:
            ts_row = conn.execute(
                "SELECT ts FROM analytics_events WHERE event = 'recent_probe_now' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert ts_row and str(ts_row[0]).endswith("+00:00")

        sources = db.get_source_stats(since, until, limit=10)
        assert isinstance(sources, list)

        sample_lead = Lead(
            title="Backend Engineer",
            description="Python and SQL required",
            budget=None,
            source="smoke",
            url=None,
        )
        text_min_level = _teaser_text(
            sample_lead,
            "LOW",
            "min_skill_matches",
            required_matches=3,
            found_matches=2,
        )
        text_other = _teaser_text(sample_lead, "LOW", "quota")
        assert "Not enough matching skills (need 3, found 2)." in text_min_level
        assert "Tune your minimum skill matches to receive more leads." in text_min_level
        assert "PRO gives unlimited daily leads and unlocks more leads." in text_other
        assert "cap" not in text_min_level.lower()
        assert "cap" not in text_other.lower()
        assert "FREE: 3/day. PRO: Unlimited." in text_min_level
        assert "FREE: 3/day. PRO: Unlimited." in text_other

        user_skills = ["python", "django", "react", "postgresql", "docker"]
        min_required = 3
        lead_overlap_2 = Lead(
            title="Need Python and React developer",
            description="Short task",
            budget=None,
            source="smoke",
            url=None,
        )
        lead_overlap_3 = Lead(
            title="Need Python Django React engineer",
            description="Medium task",
            budget=None,
            source="smoke",
            url=None,
        )
        lead_overlap_5 = Lead(
            title="Python Django React PostgreSQL Docker expert",
            description="Complex task",
            budget=None,
            source="smoke",
            url=None,
        )
        overlap_2 = len(match_lead(user_skills, lead_overlap_2).get("matched") or [])
        overlap_3 = len(match_lead(user_skills, lead_overlap_3).get("matched") or [])
        overlap_5 = len(match_lead(user_skills, lead_overlap_5).get("matched") or [])
        free_cap = effective_cap("FREE", None)
        assert can_send_notification("FREE", overlap_2, sent_today=0, min_skill_matches=min_required, cap=free_cap)[0] is False
        assert can_send_notification("FREE", overlap_3, sent_today=0, min_skill_matches=min_required, cap=free_cap)[0] is True
        assert can_send_notification("FREE", overlap_5, sent_today=0, min_skill_matches=5, cap=free_cap)[0] is True

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

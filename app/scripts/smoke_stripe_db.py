"""Smoke test for Stripe billing DB primitives."""

from __future__ import annotations

import os
import tempfile

from app import db


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "stripe_smoke.db")
        db.configure_db(path)
        db.init_db()

        assert db.mark_event_processed("stripe", "evt_1") is True
        assert db.mark_event_processed("stripe", "evt_1") is False

        session = {
            "id": "cs_test_1",
            "metadata": {"user_id": "123", "request_id": "77"},
            "payment_intent": "pi_1",
            "subscription": "sub_1",
            "customer": "cus_1",
            "amount_total": 1200,
            "currency": "usd",
        }
        db.upsert_payment_from_checkout(session)
        assert db.mark_payment_paid_by_session("cs_test_1", subscription_id="sub_1") is True

        uid = db.get_payment_user_id_by_subscription_id("sub_1")
        assert uid == 123

        db.upsert_stripe_subscription(123, "sub_1", "cus_1", "active", None)
        assert db.get_subscription_user_id("sub_1") == 123

    print("smoke_stripe_db: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Smoke test for error telemetry storage and sanitization."""

from __future__ import annotations

import json
import os
import tempfile

from app import db


def main() -> int:
    old_db_path = db.DB_PATH
    old_db_uri = db.DB_URI
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "errors_smoke.db")
            db.configure_db(path)
            db.init_db()

            try:
                raise RuntimeError("Payment failed for user john.doe@example.com with token=abc123")
            except Exception as exc:
                db.record_error(
                    "billing",
                    exc,
                    context={
                        "email": "john.doe@example.com",
                        "stripe_secret": "sk_test_1234567890",
                        "notes": "x" * 300,
                    },
                )

            large_context = {
                f"key_{idx}": "y" * 512
                for idx in range(64)
            }
            try:
                raise ValueError("Large context test")
            except Exception as exc:
                db.record_error("monitoring", exc, context=large_context)

            rows = db.get_recent_errors(limit=5)
            assert rows, "expected at least one stored error"
            assert rows[0]["component"] in {"billing", "monitoring"}

            with db._connect() as conn:
                row = conn.execute(
                    "SELECT context_json FROM error_events ORDER BY id DESC LIMIT 1"
                ).fetchone()
                first_count_row = conn.execute("SELECT COUNT(*) FROM error_events").fetchone()
            assert row and row[0], "expected context_json"
            context = json.loads(str(row[0]))
            if "stripe_secret" in context:
                assert context.get("stripe_secret") == "[redacted]"
                assert context.get("email") == "[redacted_email]"
                assert str(context.get("notes", "")).endswith("...")
            else:
                assert context.get("truncated") is True
            assert len(str(row[0]).encode("utf-8")) <= db.MAX_ERROR_CONTEXT_BYTES

            try:
                raise RuntimeError("DEDUPE_ME")
            except Exception as exc:
                db.record_error("billing", exc, context={"source": "smoke"})
            try:
                raise RuntimeError("DEDUPE_ME")
            except Exception as exc:
                db.record_error("billing", exc, context={"source": "smoke"})

            with db._connect() as conn:
                dedupe_count = conn.execute(
                    "SELECT COUNT(*) FROM error_events WHERE component='billing' AND message LIKE 'RuntimeError: DEDUPE_ME%'"
                ).fetchone()
            assert dedupe_count and int(dedupe_count[0]) == 1, "expected dedupe to suppress duplicate error insert"

            base_ts = "2026-01-01T00:00:00+00:00"
            later_ts = "2026-01-01T00:10:00+00:00"
            try:
                raise RuntimeError("OUTSIDE_WINDOW")
            except Exception as exc:
                db.record_error("billing", exc, context={"source": "smoke"}, ts=base_ts)
            try:
                raise RuntimeError("OUTSIDE_WINDOW")
            except Exception as exc:
                db.record_error("billing", exc, context={"source": "smoke"}, ts=later_ts)
            with db._connect() as conn:
                outside_rows = conn.execute(
                    "SELECT COUNT(*) FROM error_events WHERE component='billing' AND message LIKE 'RuntimeError: OUTSIDE_WINDOW%'"
                ).fetchone()
            assert outside_rows and int(outside_rows[0]) == 2, "expected inserts outside dedupe window"

            assert first_count_row and int(first_count_row[0]) >= 2

            summary = db.get_error_summary("1970-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00", limit=3)
            assert summary
            assert db.get_error_count("1970-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00") >= 1
    finally:
        db.configure_db(old_db_path, uri=old_db_uri)

    print("smoke_errors_db: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

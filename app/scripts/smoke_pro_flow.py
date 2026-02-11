"""Smoke test for PRO upgrade approval flow.

Run:
    python -m app.scripts.smoke_pro_flow
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time

from app import db


def _print(tag: str, message: str) -> None:
    print(f"[{tag}] {message}", flush=True)


def _resolve_db_target() -> tuple[str, bool, sqlite3.Connection | None]:
    raw = os.getenv("SMOKE_DB_PATH", ":memory:").strip() or ":memory:"
    if raw == ":memory:":
        uri = f"file:smoke_pro_flow_{int(time.time() * 1000)}?mode=memory&cache=shared"
        anchor = sqlite3.connect(uri, uri=True)
        _print("INFO", "Using shared in-memory SQLite database")
        return uri, True, anchor

    if raw.startswith("file:"):
        _print("INFO", f"Using SQLite URI: {raw}")
        return raw, True, None

    if os.path.exists(raw):
        os.remove(raw)
        _print("INFO", f"Removed existing DB file: {raw}")
    _print("INFO", f"Using SQLite file: {raw}")
    return raw, False, None


def _query_all(path: str, uri: bool, sql: str, params: tuple = ()) -> list[tuple]:
    with sqlite3.connect(path, uri=uri) as conn:
        return conn.execute(sql, params).fetchall()


async def main() -> int:
    failed = False
    warnings = 0
    anchor_conn: sqlite3.Connection | None = None

    try:
        path, uri, anchor_conn = _resolve_db_target()
        db.configure_db(path, uri=uri)
        db.init_db()
        _print("OK", "Initialized DB schema/migrations")

        # A) Default plan is FREE.
        user_a = 500_001
        plan = db.get_user_plan(user_a)
        assert plan == "FREE", f"expected FREE, got {plan}"
        _print("OK", "A) default plan invariant")

        # B) Pending uniqueness invariant.
        snapshot = json.dumps({"source": "smoke"})
        req_id_1 = db.create_upgrade_request(user_a, "@smoke_a", snapshot, paid=0)
        req_id_2 = db.create_upgrade_request(user_a, "@smoke_a", snapshot, paid=0)
        assert req_id_1 == req_id_2, "duplicate pending returned different IDs"
        rows = _query_all(
            path,
            uri,
            "SELECT id, status FROM upgrade_requests WHERE user_id = ? AND status = 'pending'",
            (user_a,),
        )
        assert len(rows) == 1, f"expected 1 pending row, got {len(rows)}"
        assert rows[0][1] == "pending", f"expected pending status, got {rows[0][1]}"
        _print("OK", "B) upgrade request uniqueness invariant")

        # C) Approve flow invariants.
        decided_at = "2026-02-11T00:00:00+00:00"
        approved = db.decide_upgrade_request(
            req_id_1,
            "approved",
            admin_id=900_001,
            admin_note="approved in smoke test",
            decided_at_iso=decided_at,
        )
        assert approved is True, "decide_upgrade_request returned False for pending approve"
        db.mark_user_pro(user_a, enabled=True, activated_at_iso=decided_at, plan="PRO")
        req = db.get_upgrade_request_by_id(req_id_1)
        assert req is not None, "approved request not found"
        assert req["status"] == "approved", f"expected approved, got {req['status']}"
        assert req["decided_at"], "decided_at is empty"
        assert req["admin_id"] == 900_001, f"unexpected admin_id {req['admin_id']}"
        assert db.get_user_plan(user_a) == "PRO", "plan was not activated to PRO"
        pending_ids = {row["id"] for row in db.list_pending_upgrade_requests(limit=200)}
        assert req_id_1 not in pending_ids, "approved request still appears in pending list"
        _print("OK", "C) approve flow invariants")

        # D) Reject flow invariants.
        user_b = 500_002
        req_b = db.create_upgrade_request(user_b, None, snapshot, paid=1)
        rejected = db.decide_upgrade_request(
            req_b,
            "rejected",
            admin_id=900_002,
            admin_note="missing payment proof",
            decided_at_iso="2026-02-11T00:05:00+00:00",
        )
        assert rejected is True, "decide_upgrade_request returned False for pending reject"
        req = db.get_upgrade_request_by_id(req_b)
        assert req is not None, "rejected request not found"
        assert req["status"] == "rejected", f"expected rejected, got {req['status']}"
        assert req["admin_note"] == "missing payment proof", "reject note mismatch"
        pending_ids = {row["id"] for row in db.list_pending_upgrade_requests(limit=200)}
        assert req_b not in pending_ids, "rejected request still appears in pending list"
        assert db.get_user_plan(user_b) == "FREE", "rejected user plan should remain FREE"
        _print("OK", "D) reject flow invariants")

        # E) State machine safety.
        second_decision_approved = db.decide_upgrade_request(
            req_id_1,
            "rejected",
            admin_id=900_003,
            admin_note="should not apply",
            decided_at_iso="2026-02-11T00:10:00+00:00",
        )
        if second_decision_approved:
            failed = True
            _print("FAIL", "E) approved request accepted a second decision")
        else:
            _print("OK", "E) approved request refused second decision")

        second_decision_rejected = db.decide_upgrade_request(
            req_b,
            "approved",
            admin_id=900_003,
            admin_note="should not apply",
            decided_at_iso="2026-02-11T00:10:01+00:00",
        )
        if second_decision_rejected:
            failed = True
            _print("FAIL", "E) rejected request accepted a second decision")
        else:
            _print("OK", "E) rejected request refused second decision")

        req1_post = db.get_upgrade_request_by_id(req_id_1)
        reqb_post = db.get_upgrade_request_by_id(req_b)
        assert req1_post and req1_post["status"] == "approved", "approved status mutated unexpectedly"
        assert reqb_post and reqb_post["status"] == "rejected", "rejected status mutated unexpectedly"

        # F) Best-effort concurrency-ish uniqueness.
        user_c = 500_003
        async def _create_pending() -> int:
            await asyncio.sleep(0)
            return db.create_upgrade_request(user_c, "@smoke_c", snapshot, 0)

        result_1, result_2 = await asyncio.gather(_create_pending(), _create_pending())
        assert result_1 == result_2, "concurrency create returned different IDs"
        count_rows = _query_all(
            path,
            uri,
            "SELECT COUNT(*) FROM upgrade_requests WHERE user_id = ? AND status = 'pending'",
            (user_c,),
        )
        assert int(count_rows[0][0]) == 1, "concurrency create produced multiple pending rows"
        _print("OK", "F) concurrency-ish uniqueness check")

        # G) Index existence best-effort.
        tables = _query_all(
            path,
            uri,
            "SELECT name FROM sqlite_master WHERE type='table' AND name='upgrade_requests'",
        )
        assert tables, "upgrade_requests table not found"
        _print("OK", "G) upgrade_requests table exists")

        idx_rows = _query_all(
            path,
            uri,
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='upgrade_requests'",
        )
        idx_map = {str(row[0]): (str(row[1]) if row[1] else "") for row in idx_rows}
        if "idx_upgrade_requests_status_created_at" in idx_map:
            _print("OK", "G) status+created_at index exists")
        else:
            failed = True
            _print("FAIL", "G) missing status+created_at index")

        has_partial = "idx_upgrade_requests_unique_pending" in idx_map and "WHERE status = 'pending'" in idx_map.get(
            "idx_upgrade_requests_unique_pending", ""
        )
        has_fallback = "idx_upgrade_requests_user_status" in idx_map
        if has_partial:
            _print("OK", "G) partial unique pending index exists")
        elif has_fallback:
            warnings += 1
            _print("INFO", "G) partial index unavailable, fallback user_status index exists")
        else:
            failed = True
            _print("FAIL", "G) missing both partial and fallback pending-related indexes")

    except AssertionError as exc:
        failed = True
        _print("FAIL", f"Assertion failed: {exc}")
    except Exception as exc:
        failed = True
        _print("FAIL", f"Unexpected error: {exc}")
    finally:
        if anchor_conn is not None:
            anchor_conn.close()

    if failed:
        _print("INFO", f"Warnings: {warnings}")
        print("SMOKE TEST FAIL")
        return 1

    if warnings:
        _print("WARN", f"Completed with {warnings} warning(s)")
    print("SMOKE TEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

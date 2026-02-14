"""Smoke test for SQLite online backup snapshots."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from app import db
from app.ops.backup import backup_db


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def main() -> int:
    old_db_path = db.DB_PATH
    old_db_uri = db.DB_URI
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src_path = os.path.join(tmp, "source.db")
            backup_path = os.path.join(tmp, "backup", "snapshot.db")

            db.configure_db(src_path)
            db.init_db()

            db.log_event("lead_ingested", user_id=1)
            db.log_event("lead_sent", user_id=1)
            with db._connect() as conn:
                conn.execute(
                    "INSERT INTO monitor_state(key, value, updated_at) VALUES (?, ?, ?)",
                    ("smoke", "ok", datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()

            out = backup_db(backup_path)
            assert os.path.exists(out), "backup file was not created"

            with sqlite3.connect(out) as conn:
                assert _table_exists(conn, "analytics_events")
                assert _table_exists(conn, "monitor_state")
                analytics_count = conn.execute("SELECT COUNT(*) FROM analytics_events").fetchone()
                monitor_count = conn.execute("SELECT COUNT(*) FROM monitor_state").fetchone()

            assert int(analytics_count[0]) == 2
            assert int(monitor_count[0]) == 1
    finally:
        db.configure_db(old_db_path, uri=old_db_uri)

    print("smoke_backup_db: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

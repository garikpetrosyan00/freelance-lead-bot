"""Smoke test for unified skills storage read/write paths."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app import db


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "smoke_skill_storage.db"
        db.configure_db(str(db_path))
        db.init_db()

        user_id = 8080
        expected = ["python", "django", "react"]
        db.set_skills(user_id, expected)

        read_back = db.get_skills(user_id)
        assert read_back == expected

        snapshot = db.get_skills_storage_snapshot(user_id)
        assert snapshot["row_exists"] is True
        assert "python" in str(snapshot["raw_skills"])

    print("smoke_skill_storage_consistency: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

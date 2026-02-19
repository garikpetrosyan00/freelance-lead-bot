"""Smoke test for UI skills picker persistence on Done."""

from __future__ import annotations

import tempfile
from pathlib import Path

from app import db


def main() -> int:
    try:
        from app.ui.skills_picker import ACTIVE_SKILL_PICKERS, init_picker_state
        from app.handlers.ui_skills_picker import _persist_picker_selection
    except ModuleNotFoundError:
        print("smoke_skills_picker_persist: SKIPPED (aiogram not installed)")
        return 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "smoke_skills_picker.db"
        db.configure_db(str(db_path))
        db.init_db()

        user_id = 4242
        state = init_picker_state(user_id, [])

        # Simulate checking two skills in the inline picker.
        state["selected"].add("python")
        state["selected"].add("django")

        saved = _persist_picker_selection(user_id, state)
        assert "python" in saved
        assert "django" in saved
        assert db.get_skills(user_id) == saved

        # Missing picker state must not overwrite persisted skills.
        ACTIVE_SKILL_PICKERS.pop(user_id, None)
        saved_after_missing_state = _persist_picker_selection(user_id, None)
        assert saved_after_missing_state == saved

    print("smoke_skills_picker_persist: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

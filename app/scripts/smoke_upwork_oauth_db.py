"""Smoke test for Upwork OAuth and saved search DB primitives."""

from __future__ import annotations

import os
import tempfile

from app import db
from app.ops.crypto import decrypt_str, encrypt_str, load_fernet


def main() -> int:
    try:
        load_fernet()
    except RuntimeError as exc:
        print(f"smoke_upwork_oauth_db: FAILED: {exc}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "upwork_oauth_smoke.db")
        db.configure_db(path)
        db.init_db()

        user_id = 321

        db.upwork_profiles_add(user_id, "default", "python telegram", '{"sort":"recency"}')
        profiles = db.upwork_profiles_list(user_id)
        assert len(profiles) == 1
        profile_id = int(profiles[0]["id"])
        assert profiles[0]["name"] == "default"
        assert db.upwork_profiles_set_enabled(user_id, "default", enabled=False) is True
        db.upwork_profiles_update_cursor(profile_id, '{"after":"cursor-1"}', "2026-02-23T10:00:00+00:00")
        profiles = db.upwork_profiles_list(user_id)
        assert profiles[0]["is_enabled"] == 0
        assert profiles[0]["last_cursor_json"] == '{"after":"cursor-1"}'

        db.upwork_oauth_state_create(
            state="state-valid",
            user_id=user_id,
            created_at_iso="2026-02-23T10:00:00+00:00",
            expires_at_iso="2026-02-23T10:10:00+00:00",
            redirect_context='{"screen":"connect"}',
        )
        popped = db.upwork_oauth_state_pop_valid("state-valid", "2026-02-23T10:05:00+00:00")
        assert popped is not None
        assert popped["user_id"] == user_id
        assert db.upwork_oauth_state_pop_valid("state-valid", "2026-02-23T10:05:00+00:00") is None

        db.upwork_oauth_state_create(
            state="state-expired",
            user_id=user_id,
            created_at_iso="2026-02-23T09:00:00+00:00",
            expires_at_iso="2026-02-23T09:30:00+00:00",
            redirect_context=None,
        )
        deleted = db.upwork_oauth_state_cleanup("2026-02-23T10:05:00+00:00")
        assert deleted >= 1

        access_plain = "dummy_access_token_for_smoke_test_1234567890"
        refresh_plain = "dummy_refresh_token_for_smoke_test_1234567890"
        db.upwork_oauth_upsert_account_connected(
            user_id=user_id,
            access_token_enc=encrypt_str(access_plain),
            refresh_token_enc=encrypt_str(refresh_plain),
            expires_at_iso="2026-02-23T11:00:00+00:00",
            scopes="jobs profile",
            tenant_id="tenant-123",
        )
        account = db.upwork_oauth_get_account(user_id)
        assert account is not None
        assert account["is_connected"] == 1
        assert account["tenant_id"] == "tenant-123"
        assert decrypt_str(str(account["access_token_enc"])) == access_plain
        assert decrypt_str(str(account["refresh_token_enc"])) == refresh_plain

        db.upwork_oauth_set_tenant_id(user_id, "tenant-456")
        account = db.upwork_oauth_get_account(user_id)
        assert account is not None
        assert account["tenant_id"] == "tenant-456"

        db.upwork_oauth_mark_disconnected(user_id, "2026-02-23T11:05:00+00:00")
        account = db.upwork_oauth_get_account(user_id)
        assert account is not None
        assert account["is_connected"] == 0
        assert account["access_token_enc"] is None
        assert account["refresh_token_enc"] is None
        assert account["access_token_expires_at"] is None
        assert account["scopes"] is None
        assert account["revoked_at"] == "2026-02-23T11:05:00+00:00"

        assert db.upwork_profiles_delete(user_id, "default") is True
        assert db.upwork_profiles_delete(user_id, "default") is False

    print("smoke_upwork_oauth_db: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

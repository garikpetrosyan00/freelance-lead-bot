"""Smoke test for per-user Upwork token refresh lock."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app import db
from app.integrations.upwork import client as upwork_client
from app.ops.crypto import encrypt_str, load_fernet


async def _run() -> None:
    user_id = 777001
    # Expired token to force refresh.
    db.upwork_oauth_upsert_account_connected(
        user_id=user_id,
        access_token_enc=encrypt_str("old_access"),
        refresh_token_enc=encrypt_str("old_refresh"),
        expires_at_iso="2020-01-01T00:00:00+00:00",
        scopes="jobs profile",
        tenant_id=None,
    )

    refresh_calls = {"count": 0}
    original_refresh = upwork_client.refresh_access_token
    upwork_client._REFRESH_LOCKS.clear()  # type: ignore[attr-defined]

    async def _fake_refresh(_refresh_token: str) -> upwork_client.TokenBundle:
        refresh_calls["count"] += 1
        await asyncio.sleep(0.15)
        return upwork_client.TokenBundle(
            access_token="new_access_once",
            refresh_token="new_refresh_once",
            token_type="Bearer",
            expires_in=3600,
            obtained_at_iso="2026-02-23T12:00:00+00:00",
            expires_at_iso="2026-02-23T13:00:00+00:00",
        )

    upwork_client.refresh_access_token = _fake_refresh
    try:
        t1, t2 = await asyncio.gather(
            upwork_client.get_valid_access_token_for_user(user_id),
            upwork_client.get_valid_access_token_for_user(user_id),
        )
    finally:
        upwork_client.refresh_access_token = original_refresh

    assert t1 == "new_access_once"
    assert t2 == "new_access_once"
    assert refresh_calls["count"] == 1, "expected exactly one refresh call"


def main() -> int:
    try:
        load_fernet()
    except RuntimeError as exc:
        print(f"smoke_upwork_refresh_lock: FAILED: {exc}")
        return 1

    old_db_path = db.DB_PATH
    old_db_uri = db.DB_URI
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "smoke_upwork_refresh_lock.db"
            db.configure_db(str(db_path))
            db.init_db()
            asyncio.run(_run())
    except Exception as exc:
        print(f"smoke_upwork_refresh_lock: FAILED: {exc}")
        return 1
    finally:
        db.configure_db(old_db_path, uri=old_db_uri)

    print("smoke_upwork_refresh_lock: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

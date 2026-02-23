"""Smoke test for Upwork API poller cycle with stubbed search."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

class _MockBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, user_id: int, text: str, reply_markup):  # noqa: ANN001
        self.messages.append({"user_id": user_id, "text": text, "reply_markup": reply_markup})
        return None


async def _run_cycle_smoke() -> None:
    from app import db
    from app.integrations.upwork.client import UpworkAuthError
    from app.pollers import upwork_api_poller

    old_cap = os.getenv("UPWORK_RSS_FREE_DAILY_CAP")
    os.environ["UPWORK_RSS_FREE_DAILY_CAP"] = "1"
    try:
        user_id = 9001
        db.set_skills(user_id, ["react", "python"])
        db.upwork_profiles_add(user_id, "smoke", "react developer", None)
        db.set_upwork_mute_keywords(user_id, [])

        # Keep smoke independent from OAuth crypto/env by seeding a connected row directly.
        with db._connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO upwork_oauth_accounts (
                    user_id, created_at, updated_at, is_connected
                ) VALUES (?, ?, ?, 1)
                """,
                (user_id, "2026-02-23T10:00:00+00:00", "2026-02-23T10:00:00+00:00"),
            )
            conn.commit()

        async def _fake_search(_user_id: int, _query: str, _limit: int):
            return [
                {
                    "id": "job-1",
                    "title": "React Frontend Engineer",
                    "url": "https://www.upwork.com/jobs/~job-1",
                    "snippet": "Need strong react skills",
                    "published_at": "2026-02-23T10:00:00+00:00",
                    "job_type": "Hourly",
                    "budget": None,
                    "hourly_from": 30.0,
                    "hourly_to": 50.0,
                    "client_payment_verified": True,
                },
                {
                    "id": "job-2",
                    "title": "React Native Developer",
                    "url": "https://www.upwork.com/jobs/~job-2",
                    "snippet": "React Native + TypeScript",
                    "published_at": "2026-02-23T10:01:00+00:00",
                    "job_type": "Hourly",
                    "budget": None,
                    "hourly_from": 35.0,
                    "hourly_to": 55.0,
                    "client_payment_verified": True,
                },
            ]

        bot = _MockBot()
        state = upwork_api_poller._PollerState()  # type: ignore[attr-defined]
        semaphore = asyncio.Semaphore(4)

        stats_1 = await upwork_api_poller.run_upwork_api_poll_cycle(
            send_fn=bot.send,
            state=state,
            semaphore=semaphore,
            search_fn=_fake_search,
        )
        assert stats_1["jobs_fetched"] >= 2
        assert stats_1["new_jobs"] == 1
        assert stats_1["sent"] == 1, "daily cap=1 should allow only one send"
        assert stats_1["filtered_out_by_watermark"] == 0
        assert len(bot.messages) == 1
        rows_after_1 = db.upwork_profiles_list(user_id)
        assert rows_after_1, "expected profile row after first cycle"
        assert rows_after_1[0]["last_cursor_json"], "expected cursor to be updated after first cycle"

        stats_2 = await upwork_api_poller.run_upwork_api_poll_cycle(
            send_fn=bot.send,
            state=state,
            semaphore=semaphore,
            search_fn=_fake_search,
        )
        assert stats_2["filtered_out_by_watermark"] >= 1
        assert stats_2["new_jobs"] == 0, "dedupe should prevent reprocessing seen jobs"
        assert stats_2["sent"] == 0
        assert len(bot.messages) == 1

        # Auth-error path: disconnect + notify-once cooldown behavior.
        db.upwork_profiles_set_enabled(user_id, "smoke", enabled=False)
        auth_user_id = 9002
        db.set_skills(auth_user_id, ["react"])
        db.upwork_profiles_add(auth_user_id, "auth-smoke", "react", None)
        with db._connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO upwork_oauth_accounts (
                    user_id, created_at, updated_at, is_connected
                ) VALUES (?, ?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    is_connected=1
                """,
                (auth_user_id, "2026-02-23T10:00:00+00:00", "2026-02-23T10:00:00+00:00"),
            )
            conn.commit()

        async def _fake_auth_error(_user_id: int, _query: str, _limit: int):
            raise UpworkAuthError(401)

        auth_state = upwork_api_poller._PollerState()  # type: ignore[attr-defined]
        auth_bot = _MockBot()
        auth_stats_1 = await upwork_api_poller.run_upwork_api_poll_cycle(
            send_fn=auth_bot.send,
            state=auth_state,
            semaphore=semaphore,
            search_fn=_fake_auth_error,
        )
        assert auth_stats_1["auth_errors"] == 1
        account = db.upwork_oauth_get_account(auth_user_id)
        assert account is not None
        assert int(account.get("is_connected") or 0) == 0
        reconnect_msgs = [m for m in auth_bot.messages if "/upwork_connect" in str(m.get("text") or "")]
        assert len(reconnect_msgs) == 1

        # Re-enable account quickly; second cycle should not notify again due to cooldown.
        with db._connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                "UPDATE upwork_oauth_accounts SET is_connected=1, updated_at=? WHERE user_id=?",
                ("2026-02-23T10:05:00+00:00", auth_user_id),
            )
            conn.commit()

        auth_stats_2 = await upwork_api_poller.run_upwork_api_poll_cycle(
            send_fn=auth_bot.send,
            state=auth_state,
            semaphore=semaphore,
            search_fn=_fake_auth_error,
        )
        assert auth_stats_2["auth_errors"] == 1
        reconnect_msgs = [m for m in auth_bot.messages if "/upwork_connect" in str(m.get("text") or "")]
        assert len(reconnect_msgs) == 1, "notify-once cooldown should suppress second auth notification"
    finally:
        if old_cap is None:
            os.environ.pop("UPWORK_RSS_FREE_DAILY_CAP", None)
        else:
            os.environ["UPWORK_RSS_FREE_DAILY_CAP"] = old_cap


def main() -> int:
    try:
        from app import db
    except Exception as exc:
        print(f"FAILED: {exc}")
        return 1
    old_db_path = db.DB_PATH
    old_db_uri = db.DB_URI
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "smoke_upwork_api_poller.db"
            db.configure_db(str(db_path))
            db.init_db()
            try:
                asyncio.run(_run_cycle_smoke())
            except Exception as exc:
                print(f"FAILED: {exc}")
                return 1
    finally:
        db.configure_db(old_db_path, uri=old_db_uri)
    print("smoke_upwork_api_poller: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

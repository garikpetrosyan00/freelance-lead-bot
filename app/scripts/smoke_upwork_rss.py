"""Smoke tests for Upwork RSS parsing, prefs filtering, retention cleanup, and poller cancellation."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app import db
from app.jobs.upwork_rss import JobItem, parse_feed_items_from_text

SAMPLE_RSS = """<?xml version='1.0' encoding='UTF-8'?>
<rss version='2.0'>
  <channel>
    <title>Upwork Jobs</title>
    <item>
      <guid>job-123</guid>
      <title>Python Telegram Bot Developer</title>
      <link>https://www.upwork.com/jobs/~0123456789abcdef</link>
      <description><![CDATA[Need <b>aiogram</b> and sqlite experience.]]></description>
      <pubDate>Mon, 17 Feb 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Backend Engineer</title>
      <link>https://www.upwork.com/jobs/~0abcdef123456789</link>
      <description>Build APIs and webhooks</description>
      <pubDate>Mon, 17 Feb 2026 11:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


class _MockBot:
    def __init__(self) -> None:
        self.sent_messages = 0

    async def send_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.sent_messages += 1
        return None


async def _smoke_poller_cancel_and_mute() -> None:
    try:
        from app.jobs import poller as poller_module
    except ModuleNotFoundError:
        return

    original_fetch = poller_module.fetch_feed_items
    original_poll_seconds = poller_module.get_upwork_rss_poll_seconds

    muted_item = JobItem(
        uid="mute-job-1",
        title="WordPress quick fix",
        link="https://www.upwork.com/jobs/~mutejob",
        summary="Need wordpress + woocommerce expert",
        published_at=None,
    )

    async def _fake_fetch(_rss_url: str):
        return [muted_item]

    try:
        db.set_skills(1001, ["wordpress"])
        db.add_upwork_feed(1001, "https://www.upwork.com/ab/jobs/rss/search?x=1", title="smoke")
        db.set_upwork_mute_keywords(1001, ["wordpress"])

        poller_module.fetch_feed_items = _fake_fetch
        poller_module.get_upwork_rss_poll_seconds = lambda: 1

        bot = _MockBot()
        # Cycle 1: muted => not sent and not marked seen.
        task_1 = asyncio.create_task(poller_module.run_upwork_rss_poller(bot))
        await asyncio.sleep(0.4)
        task_1.cancel()
        try:
            await task_1
        except asyncio.CancelledError:
            pass
        assert bot.sent_messages == 0
        assert db.is_upwork_job_seen(1001, muted_item.uid) is False

        # Cycle 2: unmuted => sent and marked seen.
        db.set_upwork_mute_keywords(1001, [])
        task_2 = asyncio.create_task(poller_module.run_upwork_rss_poller(bot))
        await asyncio.sleep(0.4)
        task_2.cancel()
        try:
            await task_2
        except asyncio.CancelledError:
            pass
        assert bot.sent_messages == 1
        assert db.is_upwork_job_seen(1001, muted_item.uid) is True

        # Cycle 3: already seen => no duplicate send.
        task_3 = asyncio.create_task(poller_module.run_upwork_rss_poller(bot))
        await asyncio.sleep(0.4)
        task_3.cancel()
        try:
            await task_3
        except asyncio.CancelledError:
            pass
        assert bot.sent_messages == 1
    finally:
        poller_module.fetch_feed_items = original_fetch
        poller_module.get_upwork_rss_poll_seconds = original_poll_seconds


def _smoke_cleanup_sql() -> None:
    with db._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            "INSERT OR REPLACE INTO upwork_jobs_seen (user_id, job_uid, first_seen_at) VALUES (?, ?, ?)",
            (5001, "old-job", "2020-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO upwork_daily_counters (user_id, day_key, alerts_sent) VALUES (?, ?, ?)",
            (5001, "2020-01-01", 3),
        )
        conn.commit()

    removed_seen = db.cleanup_upwork_jobs_seen_before("2021-01-01T00:00:00+00:00")
    removed_counters = db.cleanup_upwork_daily_counters_before("2021-01-01")
    assert removed_seen >= 1
    assert removed_counters >= 1


def _smoke_seen_prefetch_list() -> None:
    with db._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            "INSERT OR REPLACE INTO upwork_jobs_seen (user_id, job_uid, first_seen_at) VALUES (?, ?, ?)",
            (7001, "uid-old", "2020-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO upwork_jobs_seen (user_id, job_uid, first_seen_at) VALUES (?, ?, ?)",
            (7001, "uid-new", "2030-01-01T00:00:00+00:00"),
        )
        conn.commit()

    rows = db.list_upwork_jobs_seen_since(7001, "2025-01-01T00:00:00+00:00")
    assert "uid-new" in rows
    assert "uid-old" not in rows


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "smoke_upwork.db"
        db.configure_db(str(db_path))
        db.init_db()

        items = parse_feed_items_from_text(SAMPLE_RSS)
        assert len(items) == 2

        first = items[0]
        assert first.uid == "job-123"
        assert "aiogram" in first.summary.lower()
        assert "<b>" not in first.summary

        second = items[1]
        assert second.uid
        assert len(second.uid) >= 32

        assert db.mark_job_seen(1001, first.uid) is True
        assert db.mark_job_seen(1001, first.uid) is False

        _smoke_seen_prefetch_list()
        _smoke_cleanup_sql()
        asyncio.run(_smoke_poller_cancel_and_mute())

    print("smoke_upwork_rss: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

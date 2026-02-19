# Freelance Lead Bot

Telegram bot for freelance lead delivery with FREE/PRO gating, button-first UI, and lightweight billing analytics.
It runs on aiogram + SQLite and supports a safe local DEMO mode with external integrations disabled.

## Features
- Inline UI menu: Home, Skills, Usage, Settings, Upgrade, Help
- Button-based multi-select skills picker with custom skill add
- FREE/PRO gating (min match level + daily cap)
- Soft paywall teasers for blocked FREE leads
- Stripe checkout flow with Telegram return deep link
- Analytics events + admin funnel command (`/funnel`)
- Monitoring + diagnostics (`/health`, `/diag`, `/doctor`)
- Public version command (`/version`)
- Upwork RSS saved-search alerts with skill matching and per-user dedupe

## Quickstart (Linux/macOS)
1. Create venv and install deps:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
2. Configure env:
```bash
cp .env.example .env
```
3. Edit `.env` and set at least:
- `BOT_TOKEN=<your telegram bot token>`
- `DEMO_MODE=1`

4. Run:
```bash
python -m app.main
```

### DEMO_MODE workflow
With `DEMO_MODE=1`:
- Telethon ingestion is disabled
- Stripe webhook server is disabled
- UI remains fully usable: Home / Skills / Settings / Usage / Test lead

This is the fastest way to run locally without external credentials.

## Enabling integrations
### Telegram ingestion (Telethon)
Set in `.env`:
- `DEMO_MODE=0`
- `ENABLE_TELEGRAM_INGESTION=1`
- `TG_API_ID=<integer>`
- `TG_API_HASH=<hash>`
- `TG_SOURCE_CHATS=chat1,chat2`

If `TG_API_ID` / `TG_API_HASH` are placeholders, ingestion will fail when enabled.

### Stripe
Set in `.env`:
- `DEMO_MODE=0`
- `STRIPE_SECRET=<secret key>`
- `STRIPE_WEBHOOK_SECRET=<webhook secret>`
- `STRIPE_PRICE_ID=<price id>`
- `PUBLIC_BASE_URL=<public base URL>`
- `BOT_USERNAME=<bot username without @>` (for return deep link)

Webhook server is started by `python -m app.main` when Stripe config is valid.

## Useful commands
### User
- `/start` open UI
- `/version` app version
- `/skills` open picker
- `/test_lead` run local match demo
- `/upwork_add_rss <rss_url> [title]` add or update a saved-search feed
- `/upwork_feeds` list your feeds
- `/upwork_feed_enable <id>` enable a feed
- `/upwork_feed_disable <id>` disable a feed
- `/upwork_feed_delete <id>` delete a feed
- `/upwork_test_rss <id>` fetch and preview first 3 jobs
- `/upwork_mute <kw1, kw2, ...>` mute unwanted keywords
- `/upwork_unmute <keyword>` remove one muted keyword
- `/upwork_mute_clear` clear muted keywords
- `/upwork_digest on|off` switch per-cycle digest mode
- `/upwork_prefs` show Upwork prefs and cap

### Admin
- `/doctor` redacted integration/env readiness report
- `/funnel` monetization funnel (last 7 days)
- `/diag` runtime diagnostics

## Troubleshooting
- TG creds placeholders cause ingestion crash unless `DEMO_MODE=1` or Telegram ingestion is disabled.
- If Stripe is disabled/misconfigured, upgrade UI falls back to manual upgrade instructions.

## Upwork RSS setup
1. In Upwork Jobs search, build your query and choose RSS for the saved search feed URL.
2. Add it in Telegram:
```bash
/upwork_add_rss https://www.upwork.com/ab/jobs/rss/search?...
```
3. Optional label:
```bash
/upwork_add_rss https://www.upwork.com/ab/jobs/rss/search?... Python backend
```
4. Verify your feeds:
```bash
/upwork_feeds
```

Poller settings (optional `.env`):
- `UPWORK_RSS_POLL_SECONDS=180`
- `UPWORK_RSS_MIN_MATCHED_SKILLS=1`
- `UPWORK_RSS_FREE_DAILY_CAP=10`
- `UPWORK_RSS_PRO_DAILY_CAP=100` (`-1` means unlimited)
- `UPWORK_RSS_SEEN_RETENTION_DAYS=30`
- `UPWORK_RSS_SEEN_PREFETCH_DAYS=7` (clamped to retention days)

# Freelance Lead Bot

## What It Does
Automation system + Telegram bot that delivers real-time freelance job leads.
Built with Python, it runs as a long-lived service and polls configured lead sources, matches jobs to user-defined skills, and sends alerts to Telegram users.

## Key Features
- Multi-source ingestion: Upwork RSS saved-search feeds, Upwork API polling mode, and optional Telegram ingestion/fake ingestion for local flows.
- Filtering and matching: skill-based matching with configurable thresholds and interactive skills picker UI.
- Routing and notifications: lead delivery to Telegram users with deduplication and digest support.
- Reliability: middleware rate limiting, retry/backoff logic in pollers, startup sanity checks, and monitoring/admin diagnostics (`/health`, `/diag`, `/doctor`).
- Storage: SQLite-backed persistence layer for user prefs, plans, feeds, counters, and event/monitoring data.
- Optional plan gating: FREE/PRO limits and usage caps are implemented and can be enabled via configuration.

## High-level Architecture
`Ingestion` -> `Processing/Filtering` -> `Storage` -> `Notification/Delivery`

- Ingestion: background pollers/listeners collect leads (Upwork RSS/API, optional Telegram source ingestion).
- Processing/Filtering: normalize lead text, match against user-selected skills, enforce plan/daily-cap rules.
- Storage: persist state in SQLite (feeds, seen jobs, user skills, plans, analytics/monitoring tables).
- Notification/Delivery: send matched leads to Telegram chats via aiogram handlers/jobs.

## Demo / Screenshots
- (add screenshot of Telegram UI)
- (add screenshot of Skills Picker flow)
- (add screenshot of matched lead notification)
- (add screenshot of monitoring/admin commands output)

## Quick Start (Local)
1. Create and activate a virtual environment, then install dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
2. Create local env config from template:
```bash
cp .env.example .env
```
3. Edit local `.env` with your values (tokens/secrets stay local and must not be committed):
- `BOT_TOKEN=...`
- Optional integrations as needed (`UPWORK_*`, billing, and ingestion-related variables)
- For local-safe run, use `DEMO_MODE=1`

4. Run the Telegram bot service:
```bash
python -m app.main
```

5. Optional: run OAuth web service (for Upwork connect/callback flow):
```bash
python -m app.web
```

## Security
- Secrets are not stored in the repository.
- `.env` is git-ignored; use `.env.example` as the template.
- Keep API tokens, OAuth credentials, and webhook secrets only in local/runtime environment variables.

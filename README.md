# Freelance Lead Bot

Minimal Telegram bot using Python and aiogram v3 (polling).

## Prerequisites
- Ubuntu
- Python 3.11+

## Setup
1. Create and activate a virtual environment:
   - `python3.11 -m venv .venv`
   - `source .venv/bin/activate`
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Create `.env` and set your bot token:
   - `cp .env.example .env`
   - Edit `.env` and set `BOT_TOKEN`.

## Run
- `python -m app.main`

## Commands
- `/start`
- `/set_skills <skills...>`
  Example: `/set_skills python django react`
- `/my_skills`
- `/test_lead`
- `/subscribe`
- `/unsubscribe`
- `/status`
- `/ping_lead`
- `/plan`
- `/upgrade`
- `/my_id`
- `/request_pro`
- `/set_plan FREE|PRO` (admin only)
- `/settings`
- `/set_min_level LOW|MEDIUM|HIGH` (PRO only)
- `/set_daily_cap <N|unlimited>` (PRO only)

## Data
- SQLite database: `data/app.db`

## Notifications
- Fake ingestion generates a test lead about every ~60 seconds.
- Subscribed users receive a notification when their skills match a lead.

## Plans (FREE vs PRO)
- FREE: only MEDIUM/HIGH matches, daily cap of 5 notifications.
- PRO: LOW/MEDIUM/HIGH matches, no daily cap.

## PRO Settings
- PRO users can set a minimum match level and a custom daily cap.

## Upgrading to PRO (Manual MVP)
1. Run `/upgrade` to get the payment link/contact.
2. Pay.
3. Run `/my_id` and send your ID to the admin.
4. Admin runs `/set_plan PRO <user_id>`.

## Matching Quality
- Smarter tokenization for tech names like `node.js`, `react-native`, `c++`, `c#`.
- Synonym expansion and weighted scoring for more realistic match scores.

## Telegram Ingestion (Telethon)
1. Get `TG_API_ID` and `TG_API_HASH` from my.telegram.org.
2. Set `TG_SOURCE_CHATS` to a comma-separated list of chat usernames or IDs.
   Example: `somegroup,anothergroup` or `-100123..., -100456...`
3. On first run, Telethon will prompt in the console for your phone and login code.
4. A session file is stored at `data/telethon.session` (do not commit it).

## Ingestion Toggles
- `ENABLE_FAKE_INGESTION=0` (default)
- `ENABLE_TELEGRAM_INGESTION=1` (default)

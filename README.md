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
- `/set_plan FREE|PRO` (admin only)

## Data
- SQLite database: `data/app.db`

## Notifications
- Fake ingestion generates a test lead about every ~60 seconds.
- Subscribed users receive a notification when their skills match a lead.

## Plans (FREE vs PRO)
- FREE: only MEDIUM/HIGH matches, daily cap of 5 notifications.
- PRO: LOW/MEDIUM/HIGH matches, no daily cap.

## Telegram Ingestion (Telethon)
1. Get `TG_API_ID` and `TG_API_HASH` from my.telegram.org.
2. Set `TG_SOURCE_CHATS` to a comma-separated list of chat usernames or IDs.
   Example: `somegroup,anothergroup` or `-100123..., -100456...`
3. On first run, Telethon will prompt in the console for your phone and login code.
4. A session file is stored at `data/telethon.session` (do not commit it).

## Ingestion Toggles
- `ENABLE_FAKE_INGESTION=0` (default)
- `ENABLE_TELEGRAM_INGESTION=1` (default)

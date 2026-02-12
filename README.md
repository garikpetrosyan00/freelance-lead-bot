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

## Run Smoke Test
- `python -m app.scripts.smoke_pro_flow`

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
- `/buy_pro`
- `/payment_status`
- `/my_id`
- `/request_pro`
- `/set_plan FREE|PRO` (admin only)
- `/pro_requests` (admin only)
- `/approve_pro <id>` (admin only)
- `/reject_pro <id> [reason]` (admin only)
- `/payments_recent [limit]` (admin only)
- `/subs_past_due [limit]` (admin only)
- `/force_sync_user <user_id>` (admin only)
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

## Upgrading to PRO (Admin Approval Flow)
1. Run `/upgrade` to get the payment link/contact.
2. Pay.
3. Run `/request_pro` to create a pending request.
4. Admin reviews pending requests with `/pro_requests`.
5. Admin approves with `/approve_pro <id>` or rejects with `/reject_pro <id> [reason]`.

## Stripe Auto-Activation Flow (Task 6)
1. User runs `/buy_pro`.
2. Bot creates a Stripe Checkout Session and returns the hosted payment URL.
3. Stripe sends webhook events to `POST /webhooks/stripe`.
4. On successful payment event, bot auto-activates `PRO`, marks payment/request state in SQLite, and notifies the user in Telegram.
5. Policy implemented for failures/cancellation:
   - `customer.subscription.deleted` and `invoice.payment_failed` downgrade to `FREE`.

## Payment Status + Reconcile (Task 6C)
- User command: `/payment_status`
  - Shows current plan, latest payment status, masked session/subscription IDs, and subscription state.
  - If webhook was missed, this command attempts Stripe reconcile and auto-activates PRO when Stripe confirms payment.
- Admin diagnostics:
  - `/payments_recent [limit]`
  - `/subs_past_due [limit]`
  - `/force_sync_user <user_id>` (runs the same reconcile flow for target user)

## Manual Test Checklist (Task 5B Hardening)
- Run `/request_pro` twice from the same user and confirm the second response shows "Request already pending" with the same request ID.
- Approve a pending request via `/approve_pro <id>` and confirm `/plan` and `/settings` reflect `PRO` immediately.
- Validate command parsing with bot mention:
  - `/approve_pro@YourBot 12`
  - `/reject_pro@YourBot 12 reason`
- Validate admin authorization:
  - Non-admin calling `/pro_requests`, `/approve_pro`, or `/reject_pro` must receive `Unauthorized`.

## Stripe Configuration
Set these in `.env`:
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_ID`
- `PUBLIC_BASE_URL` (example: `https://your-domain.example`)

Optional:
- `STRIPE_MODE=subscription` (default) or `payment`
- `STRIPE_CURRENCY=usd`
- `STRIPE_SUCCESS_PATH=/stripe/success`
- `STRIPE_CANCEL_PATH=/stripe/cancel`
- `WEBHOOK_HOST=0.0.0.0`
- `WEBHOOK_PORT=8080`

If Stripe env vars are missing, bot still starts normally; `/buy_pro` returns a friendly unavailable message.

## Run Stripe Webhook Locally (ngrok)
1. Start bot:
   - `python -m app.main`
2. Expose local webhook port:
   - `ngrok http 8080`
3. Set `PUBLIC_BASE_URL` to your ngrok HTTPS URL.
4. In Stripe Dashboard, create webhook endpoint:
   - `https://<your-ngrok-domain>/webhooks/stripe`
5. Subscribe at least to:
   - `checkout.session.completed`
   - `invoice.paid`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
6. Copy signing secret into `.env` as `STRIPE_WEBHOOK_SECRET`.

## Manual Test Checklist (Stripe)
1. Run `/buy_pro` and complete checkout with Stripe test card.
2. Confirm user receives Telegram message: payment received and PRO activated.
3. Confirm `/payment_status` and `/plan` show `PRO`.
4. Confirm `upgrade_requests.paid=1` and request auto-approved when pending exists.
5. Replay same Stripe event and confirm idempotency (no duplicate activation side effects).
6. Trigger `invoice.payment_failed` or `customer.subscription.deleted` in Stripe test tools and confirm downgrade to `FREE`.
7. Simulate missed webhook:
   - stop webhook server, complete payment, run `/payment_status`, confirm reconcile activates PRO.
8. Admin checks:
   - `/payments_recent 20` returns latest payments.
   - `/subs_past_due 20` returns past_due subscriptions.
   - `/force_sync_user <user_id>` reports reconcile result.

## Troubleshooting Payments
- If payment completed but PRO is not active, run `/payment_status` to trigger reconcile.
- If status remains pending for several minutes, retry `/payment_status`.
- If reconcile keeps failing, check Stripe keys/webhook config and contact admin.

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

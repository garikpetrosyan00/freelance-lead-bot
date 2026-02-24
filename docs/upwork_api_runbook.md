# Upwork API Runbook

## 1) Overview
This bot supports two Upwork fetch modes:
- `UPWORK_FETCH_MODE=public` (default): parses public Upwork search pages, no OAuth required.
- `UPWORK_FETCH_MODE=oauth`: uses official Upwork OAuth2 + GraphQL APIs.

Runtime has two processes:
- Bot process (aiogram)
- Web process (FastAPI) for OAuth connect/callback endpoints

OAuth callback must be publicly reachable by Upwork only in `oauth` fetch mode.

## 2) Upwork App Setup
In your Upwork app settings:
- Configure redirect URL to exactly match:
  - `UPWORK_REDIRECT_URL`
  - Example: `https://your-domain.com/upwork/callback`

If redirect URL mismatches, OAuth code exchange fails.

## 3) Required Environment Variables
Core:
- `BOT_TOKEN`: Telegram bot token.
- `PUBLIC_BASE_URL`: Public base URL for web app (used for connect link), example `https://your-domain.com`.
- `UPWORK_CLIENT_ID`: Upwork OAuth client ID (`oauth` fetch mode only).
- `UPWORK_CLIENT_SECRET`: Upwork OAuth client secret (`oauth` fetch mode only).
- `UPWORK_REDIRECT_URL`: OAuth callback URL (usually `${PUBLIC_BASE_URL}/upwork/callback`) (`oauth` mode only).
- `UPWORK_TOKEN_ENCRYPTION_KEY`: Fernet key for token encryption at rest (`oauth` mode only).
  - Generate:
  - `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

Optional defaults:
- `UPWORK_FETCH_MODE` (`public` default, `oauth` to require official app credentials)
- `UPWORK_OAUTH_AUTHORIZE_URL` (default official authorize URL)
- `UPWORK_OAUTH_TOKEN_URL` (default official token URL)
- `UPWORK_GRAPHQL_URL` (default official GraphQL URL)
- `UPWORK_POLL_MODE` (`api` default, `rss` for legacy testing)
- `BOT_USERNAME` (optional, used in OAuth success page deep link)

## 4) Local Run (Two Terminals)
Terminal 1 (web app):
```bash
python -m app.web
```
Serves OAuth endpoints (default `0.0.0.0:8081`).

Terminal 2 (bot):
```bash
python -m app.main
```

For local OAuth end-to-end, your callback URL must be publicly reachable (for example via tunnel/reverse proxy).

## 5) Health Checks and Validation
Web:
- `GET /healthz` -> `ok`

Telegram flow:
1. `/upwork_connect` (OAuth only; public mode responds with mode info)
2. Complete OAuth in browser
3. `/upwork_status` (verify connected, token expiry, profile counts, poll mode)
4. `/upwork_test_api` (verify GraphQL call)

Profile management:
- `/upwork_profiles list`
- `/upwork_profiles add Example | react frontend`
- `/upwork_profiles enable Example`

## 6) Common Errors / Troubleshooting
- Invalid or expired state:
  - Cause: state TTL expired or reused.
  - Action: run `/upwork_connect` again.

- Missing encryption key:
  - Symptom: startup/smoke failures mention `UPWORK_TOKEN_ENCRYPTION_KEY`.
  - Action: set valid Fernet key.

- Auth revoked/expired:
  - Symptom: poller disconnects account and sends reconnect warning.
  - Action: user runs `/upwork_connect` again.

- Rate limit / transient upstream errors:
  - Poller uses retries + backoff and per-profile cooldown.
  - Action: usually wait; inspect logs if persistent.

## 7) Security Notes
- Tokens are stored encrypted at rest (Fernet) in DB (`*_enc` fields).
- No password collection; OAuth2 only.
- Logging uses redaction for tokens and authorization headers.
- Do not print/store plaintext tokens in scripts or diagnostics.

## 8) Poll Mode
- Default: `UPWORK_POLL_MODE=api` (recommended).
- Legacy mode: `UPWORK_POLL_MODE=rss` for old RSS testing only.
- Keep only one mode active to avoid duplicate sends.

## 9) Fetch Mode
- Default: `UPWORK_FETCH_MODE=public`.
- `public`: profile polling works without connected Upwork OAuth accounts.
- `oauth`: preserves existing connection requirement and auth-error disconnect/reconnect flow.

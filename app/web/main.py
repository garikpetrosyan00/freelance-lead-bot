"""Minimal web app for Upwork OAuth flow."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from html import escape

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app import db
from app.integrations.upwork.client import build_authorize_url, exchange_code_for_token
from app.ops.crypto import decrypt_str, encrypt_str
from app.ops.logging_utils import safe_exc

logger = logging.getLogger(__name__)
app = FastAPI()
_OAUTH_STATE_CLEANUP_INTERVAL_SECONDS = 3600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _html_page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    safe_title = escape(title)
    safe_body = body
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{safe_title}</title></head>"
        f"<body><h3>{safe_title}</h3><p>{safe_body}</p></body></html>"
    )
    return HTMLResponse(content=html, status_code=status_code)


async def _oauth_state_cleanup_loop() -> None:
    while True:
        try:
            db.upwork_oauth_state_cleanup(_now_iso())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            db.record_error("upwork_oauth", exc, context={"action": "web_state_cleanup"})
            logger.warning("Web OAuth state cleanup failed: %s", safe_exc(exc))
        await asyncio.sleep(_OAUTH_STATE_CLEANUP_INTERVAL_SECONDS)


@app.on_event("startup")
async def on_startup() -> None:
    # Run once immediately, then continue hourly.
    try:
        db.upwork_oauth_state_cleanup(_now_iso())
    except Exception as exc:
        db.record_error("upwork_oauth", exc, context={"action": "web_state_cleanup_startup"})
        logger.warning("Web OAuth state startup cleanup failed: %s", safe_exc(exc))
    app.state.oauth_cleanup_task = asyncio.create_task(_oauth_state_cleanup_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    task = getattr(app.state, "oauth_cleanup_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@app.get("/healthz")
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/upwork/connect")
async def upwork_connect(state: str = Query(default="")):
    now_iso = _now_iso()
    state_row = db.upwork_oauth_state_peek_valid(state=state, now_iso=now_iso)
    if state_row is None:
        return _html_page(
            "Upwork Connect",
            "Invalid or expired state. Go back to the bot and run /upwork_connect again.",
            status_code=400,
        )
    return RedirectResponse(url=build_authorize_url(state), status_code=302)


@app.get("/upwork/callback")
async def upwork_callback(code: str = Query(default=""), state: str = Query(default="")):
    now_iso = _now_iso()
    if not code.strip():
        return _html_page("Upwork Connect", "Missing OAuth code.", status_code=400)

    state_row = db.upwork_oauth_state_pop_valid(state=state, now_iso=now_iso)
    if state_row is None:
        return _html_page(
            "Upwork Connect",
            "Invalid or expired state. Go back to the bot and run /upwork_connect again.",
            status_code=400,
        )

    user_id = int(state_row["user_id"])
    account = db.upwork_oauth_get_account(user_id) or {}
    try:
        bundle = await exchange_code_for_token(code)
        next_refresh = bundle.refresh_token
        if not next_refresh:
            prior_refresh_enc = str(account.get("refresh_token_enc") or "").strip()
            if prior_refresh_enc:
                next_refresh = decrypt_str(prior_refresh_enc)
            if not next_refresh:
                raise RuntimeError("Upwork token response missing refresh token.")
        db.upwork_oauth_upsert_account_connected(
            user_id=user_id,
            access_token_enc=encrypt_str(bundle.access_token),
            refresh_token_enc=encrypt_str(next_refresh),
            expires_at_iso=bundle.expires_at_iso,
            scopes=str(account.get("scopes") or "") or None,
            tenant_id=str(account.get("tenant_id") or "") or None,
        )
    except Exception as exc:
        db.record_error(
            "upwork_oauth",
            exc,
            context={"action": "oauth_callback_exchange", "user_id": user_id},
        )
        logger.warning("Upwork OAuth callback failed: %s", safe_exc(exc))
        return _html_page(
            "Upwork Connect",
            "Connection failed. Please return to Telegram and try /upwork_connect again.",
            status_code=500,
        )

    bot_username = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
    if bot_username:
        link = f"<a href='tg://resolve?domain={escape(bot_username)}'>Open Telegram bot</a>"
        return _html_page("Upwork Connect", f"Connected ✅ You can return to Telegram. {link}")
    return _html_page("Upwork Connect", "Connected ✅ You can return to Telegram.")

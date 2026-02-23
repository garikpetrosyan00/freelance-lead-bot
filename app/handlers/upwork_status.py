"""Status command for Upwork OAuth/API visibility."""

from __future__ import annotations

import json
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app import db
from app.config import get_upwork_poll_mode

router = Router()


def _short_cursor(raw_cursor: str | None) -> str | None:
    raw = str(raw_cursor or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    value = str(payload.get("last_published_at") or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d %H:%M")


@router.message(Command("upwork_status"))
async def handle_upwork_status(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Unauthorized")
        return
    user_id = message.from_user.id
    account = db.upwork_oauth_get_account(user_id)
    connected = bool(account and int(account.get("is_connected") or 0) == 1)
    profiles = db.upwork_profiles_list(user_id)
    enabled = [row for row in profiles if int(row.get("is_enabled") or 0) == 1]

    lines = [
        f"Upwork: {'Connected ✅' if connected else 'Not connected ❌'}",
        f"Token expires at: {account.get('access_token_expires_at') if connected and account else '-'}",
        f"Profiles: total {len(profiles)}, enabled {len(enabled)}",
        f"Poll mode: {get_upwork_poll_mode()}",
    ]

    if enabled:
        preview_lines: list[str] = []
        for row in enabled[:5]:
            name = str(row.get("name") or "-")
            cursor_short = _short_cursor(str(row.get("last_cursor_json") or "") or None)
            if cursor_short:
                preview_lines.append(f"- {name} ({cursor_short})")
            else:
                preview_lines.append(f"- {name}")
        lines.append("Enabled profiles:")
        lines.extend(preview_lines)

    await message.answer("\n".join(lines))

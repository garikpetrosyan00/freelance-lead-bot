"""Upgrade request and admin approval handlers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
)
from aiogram.types import Message

from app.analytics import log_event
from app.config import get_admin_user_ids
from app.db import (
    create_upgrade_request_with_state,
    decide_upgrade_request,
    get_upgrade_request_by_id,
    get_user_plan,
    get_user_settings,
    list_pending_upgrade_requests,
    mark_user_pro,
    record_error,
)
from app.ops.logging_utils import log_kv, mask_user_id, safe_exc
from app.ops.auth import require_admin
from app.gating import FREE_DAILY_CAP

router = Router()
logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _render_username(username: str | None) -> str:
    if not username:
        return "(none)"
    return username


def _settings_snapshot_json(user_id: int, username: str | None) -> str:
    plan = get_user_plan(user_id)
    min_level, _ = get_user_settings(user_id)
    return json.dumps(
        {
            "plan": plan,
            "min_level": min_level,
            "daily_limit": None if plan == "PRO" else FREE_DAILY_CAP,
            "free_daily_cap": FREE_DAILY_CAP,
            "username": username,
        },
        ensure_ascii=True,
    )


async def _notify_admins(bot: Bot, text: str) -> None:
    admin_ids = get_admin_user_ids()
    if not admin_ids:
        logger.warning("No ADMIN_USER_IDS configured; cannot notify admins")
        return

    for admin_id in admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception as exc:
            record_error("admin", exc, context={"admin_id": admin_id, "action": "notify_upgrade_request"})
            log_kv(
                logger,
                logging.WARNING,
                "Failed to notify admin about upgrade request",
                admin=mask_user_id(admin_id),
                error=safe_exc(exc),
            )


async def _notify_user_or_log(bot: Bot, user_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id=user_id, text=text)
        return True
    except (
        TelegramForbiddenError,
        TelegramBadRequest,
        TelegramNetworkError,
        TelegramAPIError,
    ):
        logger.warning("Failed to notify user=%s (blocked or invalid chat)", mask_user_id(user_id))
        return False
    except Exception as exc:
        record_error("notify", exc, context={"user_id": user_id, "action": "upgrade_notify_user"})
        log_kv(
            logger,
            logging.WARNING,
            "Unexpected error sending message to user",
            user=mask_user_id(user_id),
            error=safe_exc(exc),
        )
        return False


@router.message(Command("request_pro"))
async def handle_request_pro(message: Message) -> None:
    user = message.from_user
    if user is None:
        await message.answer("Unable to identify user.")
        return

    user_id = user.id
    username = f"@{user.username}" if user.username else None
    snapshot = _settings_snapshot_json(user_id, username)
    request_id, created = create_upgrade_request_with_state(
        user_id=user_id,
        username=username,
        settings_snapshot_json=snapshot,
        paid=0,
    )
    if not created:
        await message.answer(f"Request already pending. ID: {request_id}")
        return

    log_event(
        "upgrade_requested",
        user_id=user_id,
        plan=get_user_plan(user_id),
        meta={"request_id": request_id, "source": "command:/request_pro"},
    )
    await message.answer(f"PRO request created. ID: {request_id}")

    lines = [
        "New PRO upgrade request",
        f"request_id: {request_id}",
        f"user_id: {user_id}",
        f"username: {_render_username(username)}",
        "paid: 0",
        f"settings_snapshot_json: {snapshot}",
        "Actions:",
        f"/approve_pro {request_id}",
        f"/reject_pro {request_id}",
    ]
    await _notify_admins(message.bot, "\n".join(lines))


@router.message(Command("pro_requests"))
async def handle_pro_requests(message: Message, command: CommandObject) -> None:
    if not await require_admin(message):
        return

    limit = 50
    args = (command.args or "").strip()
    if args:
        if len(args.split()) != 1:
            await message.answer("Usage: /pro_requests [limit]\nExample: /pro_requests 20")
            return
        if not args.isdigit():
            await message.answer("Invalid limit. Use an integer between 1 and 200.")
            return
        limit = int(args)
        if limit < 1 or limit > 200:
            await message.answer("Invalid limit. Use an integer between 1 and 200.")
            return

    rows = list_pending_upgrade_requests(limit=limit)
    if not rows:
        await message.answer("No pending requests")
        return

    lines = ["Pending PRO requests:"]
    for row in rows:
        username = row["username"] if row["username"] else "-"
        lines.append(
            f"#{row['id']} user={row['user_id']} username={username} "
            f"created={row['created_at']} paid={row['paid']}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("approve_pro"))
async def handle_approve_pro(message: Message, command: CommandObject) -> None:
    user = message.from_user
    if not await require_admin(message) or user is None:
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer("Usage: /approve_pro <id>\nExample: /approve_pro 12")
        return
    if len(args.split()) != 1:
        await message.answer("Usage: /approve_pro <id>\nExample: /approve_pro 12")
        return
    request_id_raw = args
    if not request_id_raw.isdigit() or int(request_id_raw) <= 0:
        await message.answer("Invalid request ID.")
        return

    request_id = int(request_id_raw)
    request = get_upgrade_request_by_id(request_id)
    if request is None:
        await message.answer("Request not found.")
        return
    if request["status"] != "pending":
        await message.answer(f"Request already decided: {request['status']}.")
        return

    decided_at = _utc_now_iso()
    decided = decide_upgrade_request(
        request_id=request_id,
        new_status="approved",
        admin_id=user.id,
        admin_note=None,
        decided_at_iso=decided_at,
    )
    if not decided:
        latest = get_upgrade_request_by_id(request_id)
        state = latest["status"] if latest else "unknown"
        await message.answer(f"Request already decided: {state}.")
        return
    mark_user_pro(
        user_id=int(request["user_id"]),
        enabled=True,
        activated_at_iso=decided_at,
        plan="PRO",
    )
    log_event(
        "upgrade_approved",
        user_id=int(request["user_id"]),
        plan="PRO",
        meta={"request_id": request_id, "admin_id": user.id, "source": "admin_command"},
    )
    log_event(
        "pro_activated",
        user_id=int(request["user_id"]),
        plan="PRO",
        meta={"source": "admin_approve", "request_id": request_id},
        ts=decided_at,
    )

    notified = await _notify_user_or_log(
        message.bot,
        int(request["user_id"]),
        "Your PRO has been activated.\n"
        "Use /settings to view your limits.\n"
        "PRO includes faster cooldown and unlimited daily leads.",
    )
    if not notified:
        await message.answer(
            f"Approved request #{request_id}. PRO activated for user {request['user_id']}, "
            "but user couldn't be notified (blocked bot?)."
        )
        return
    await message.answer(f"Approved request #{request_id}. PRO activated for user {request['user_id']}.")


@router.message(Command("reject_pro"))
async def handle_reject_pro(message: Message, command: CommandObject) -> None:
    user = message.from_user
    if not await require_admin(message) or user is None:
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer("Usage: /reject_pro <id> [reason]\nExample: /reject_pro 12 duplicate payment")
        return
    parsed = args.split(maxsplit=1)
    request_id_raw = parsed[0].strip()
    if not request_id_raw.isdigit() or int(request_id_raw) <= 0:
        await message.answer("Invalid request ID.")
        return

    reason = parsed[1].strip() if len(parsed) > 1 and parsed[1].strip() else None
    request_id = int(request_id_raw)
    request = get_upgrade_request_by_id(request_id)
    if request is None:
        await message.answer("Request not found.")
        return
    if request["status"] != "pending":
        await message.answer(f"Request already decided: {request['status']}.")
        return

    decided_at = _utc_now_iso()
    decided = decide_upgrade_request(
        request_id=request_id,
        new_status="rejected",
        admin_id=user.id,
        admin_note=reason,
        decided_at_iso=decided_at,
    )
    if not decided:
        latest = get_upgrade_request_by_id(request_id)
        state = latest["status"] if latest else "unknown"
        await message.answer(f"Request already decided: {state}.")
        return
    log_event(
        "upgrade_rejected",
        user_id=int(request["user_id"]),
        plan=get_user_plan(int(request["user_id"])),
        meta={"request_id": request_id, "admin_id": user.id, "reason": reason},
        ts=decided_at,
    )

    user_text = "Request rejected."
    if reason:
        user_text = f"Request rejected.\nReason: {reason}"
    notified = await _notify_user_or_log(message.bot, int(request["user_id"]), user_text)
    if not notified:
        await message.answer(
            f"Rejected request #{request_id}, but user couldn't be notified (blocked bot?)."
        )
        return
    await message.answer(f"Rejected request #{request_id}.")

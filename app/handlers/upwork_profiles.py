"""Commands for managing Upwork API search profiles."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app import db

router = Router()

_NAME_MAX_LEN = 32
_QUERY_MAX_LEN = 200


def _subcommand_and_rest(text: str) -> tuple[str, str]:
    parts = (text or "").strip().split(maxsplit=2)
    if len(parts) < 2:
        return "", ""
    sub = parts[1].strip().lower()
    rest = parts[2].strip() if len(parts) > 2 else ""
    return sub, rest


def _parse_add_payload(payload: str) -> tuple[str | None, str | None]:
    raw = (payload or "").strip()
    if "|" not in raw:
        return None, None
    name_raw, query_raw = raw.split("|", 1)
    name = name_raw.strip()
    query = query_raw.strip()
    if not name or not query:
        return None, None
    if len(name) > _NAME_MAX_LEN or len(query) > _QUERY_MAX_LEN:
        return None, None
    return name, query


def _usage_text() -> str:
    return (
        "Usage:\n"
        "/upwork_profiles list\n"
        "/upwork_profiles add <name> | <query>\n"
        "/upwork_profiles del <name>\n"
        "/upwork_profiles enable <name>\n"
        "/upwork_profiles disable <name>"
    )


@router.message(Command("upwork_profiles"))
async def handle_upwork_profiles(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Unauthorized")
        return
    user_id = message.from_user.id
    sub, rest = _subcommand_and_rest(message.text or "")

    if not sub or sub == "list":
        rows = db.upwork_profiles_list(user_id)
        if not rows:
            await message.answer(
                "No profiles yet. Try:\n/upwork_profiles add Example | react frontend"
            )
            return
        lines = ["Profiles:"]
        for row in rows:
            status = "ON" if int(row.get("is_enabled") or 0) == 1 else "OFF"
            lines.append(f"- [{status}] {row.get('name')}: {row.get('query')}")
        await message.answer("\n".join(lines))
        return

    if sub == "add":
        name, query = _parse_add_payload(rest)
        if not name or not query:
            await message.answer(
                "Invalid format. Use:\n/upwork_profiles add <name> | <query>\n"
                "Limits: name<=32, query<=200."
            )
            return
        existing = {str(row.get("name") or "") for row in db.upwork_profiles_list(user_id)}
        if name in existing:
            await message.answer("Profile with this name already exists.")
            return
        try:
            db.upwork_profiles_add(user_id, name, query, None)
        except Exception as exc:
            db.record_error(
                "upwork_profiles",
                exc,
                context={"action": "add_profile", "user_id": user_id},
            )
            await message.answer("Could not add profile right now.")
            return
        await message.answer(f"Added profile: {name}")
        return

    if sub == "del":
        name = rest.strip()
        if not name:
            await message.answer("Usage: /upwork_profiles del <name>")
            return
        deleted = db.upwork_profiles_delete(user_id, name)
        await message.answer("Deleted." if deleted else "Profile not found.")
        return

    if sub in {"enable", "disable"}:
        name = rest.strip()
        if not name:
            await message.answer(f"Usage: /upwork_profiles {sub} <name>")
            return
        enabled = sub == "enable"
        updated = db.upwork_profiles_set_enabled(user_id, name, enabled=enabled)
        if not updated:
            await message.answer("Profile not found.")
            return
        await message.answer("Enabled." if enabled else "Disabled.")
        return

    await message.answer(_usage_text())

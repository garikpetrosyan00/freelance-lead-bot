"""/start command handler."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import db
from app.ui.screens import start_kb, welcome_text
from app.version import version_text

router = Router()


def _open_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Open menu", callback_data="ui:home")],
            [InlineKeyboardButton(text="🔄 Refresh status", callback_data="ui:home")],
        ]
    )


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    text = (message.text or "").strip()
    payload = ""
    if " " in text:
        payload = text.split(" ", 1)[1].strip()
    if payload == "pro_return":
        plan = db.get_plan(message.from_user.id)
        status_text = (
            "✅ PRO is active. Enjoy!"
            if plan == "PRO"
            else "⏳ Payment received. Activating PRO... (usually under 1 min)"
        )
        await message.answer(
            status_text,
            reply_markup=_open_menu_kb(),
        )
        return
    await message.answer(welcome_text(), reply_markup=start_kb())


@router.message(Command("version"))
async def handle_version(message: Message) -> None:
    await message.answer(f"{version_text()}\nUse /start to open menu.")


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.answer(
        "Help\n"
        "- Open menu: /start\n"
        "- Liveness check: /ping\n"
        "- Set skills: /skills or /set_skills <skills...>\n"
        "- Test matching (debug): /test_lead or /test_lead all\n"
        "- Manage Upwork RSS alerts: /upwork_feeds, /upwork_add_rss\n"
        "- Connect Upwork OAuth: /upwork_connect, /upwork_disconnect\n"
        "- Manage Upwork API profiles: /upwork_profiles list|add|del|enable|disable\n"
        "- Upwork status: /upwork_status\n"
        "- Test Upwork official API: /upwork_test_api [query]\n"
        "- Plan/settings: /plan, /settings"
    )


@router.message(Command("ping"))
async def handle_ping(message: Message) -> None:
    await message.answer("pong")

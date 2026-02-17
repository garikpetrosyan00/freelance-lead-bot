"""/start command handler."""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.ui.screens import start_kb, welcome_text

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
        await message.answer(
            "✅ Thanks! If payment completed, PRO will activate shortly.",
            reply_markup=_open_menu_kb(),
        )
        return
    await message.answer(welcome_text(), reply_markup=start_kb())

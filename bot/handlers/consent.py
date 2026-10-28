"""/start and the "Погоджуюсь" button."""
import asyncpg
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import texts
from bot.db import add_consent, has_consent

CONSENT_CALLBACK = "consent"

router = Router()


def consent_keyboard() -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(text=texts.CONSENT_BUTTON, callback_data=CONSENT_CALLBACK)
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


@router.message(CommandStart())
async def start(message: Message, pool: asyncpg.Pool) -> None:
    if await has_consent(pool, message.from_user.id):
        await message.answer(texts.HOW_TO_USE)
    else:
        await message.answer(texts.CONSENT_REQUEST, reply_markup=consent_keyboard())


@router.callback_query(F.data == CONSENT_CALLBACK)
async def agree(callback: CallbackQuery, pool: asyncpg.Pool) -> None:
    await add_consent(pool, callback.from_user.id)
    # Remove the button so it is not pressed again
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(texts.CONSENT_THANKS)
    # Tell Telegram the press was handled, otherwise the button shows a loading spinner
    await callback.answer()
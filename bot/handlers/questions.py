"""Any text message that is not a command is a question."""
from datetime import date

import asyncpg
from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from bot.llm import LLM
from bot.search import answer_question

router = Router()


@router.message(F.text & ~F.text.startswith("/"))
async def question(message: Message, pool: asyncpg.Pool, llm: LLM) -> None:
    # Keep showing "typing..." while the LLM is working: a single status disappears after 5 seconds
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        reply = await answer_question(message.text, pool, llm, date.today())
    await message.answer(reply)
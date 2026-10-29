"""Fake Telegram objects for handler tests: handlers are called directly, without Telegram."""
import contextlib
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

HEDGEHOG = 1  # Telegram user id


def make_message(text=None, *, document=None, chat_type="private", user_id=HEDGEHOG):
    message = MagicMock()
    message.text = text
    message.document = document
    message.chat.type = chat_type
    message.chat.id = user_id
    message.from_user.id = user_id
    message.from_user.full_name = "Їжак"
    # Every method the handlers await
    message.answer = AsyncMock()
    message.edit_text = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    message.bot.download = AsyncMock()
    return message


def make_callback(user_id=HEDGEHOG):
    callback = MagicMock()
    callback.from_user.id = user_id
    callback.message = make_message(user_id=user_id)
    callback.answer = AsyncMock()
    return callback


def make_state() -> FSMContext:
    """Real aiogram dialog state kept in memory, like in the bot."""
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=HEDGEHOG, user_id=HEDGEHOG))


def replies(message) -> list[str]:
    """Texts of all messages the bot sent in reply."""
    return [call.args[0] for call in message.answer.call_args_list]


def button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


class NoChatAction:
    """Replaces ChatActionSender: the real one sends "typing..." to Telegram in the background."""

    @staticmethod
    def typing(**kwargs):
        return contextlib.nullcontext()
"""Checks that run before every message handler."""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message

from bot import texts
from bot.db import has_consent

Handler = Callable[[Message, dict[str, Any]], Awaitable[Any]]


class AccessMiddleware(BaseMiddleware):
    """Only private chats, and nothing but /start until the user presses "Погоджуюсь"."""

    async def __call__(self, handler: Handler, event: Message, data: dict[str, Any]) -> Any:
        # The customer wants private chats only: ignore groups silently
        if event.chat.type != ChatType.PRIVATE:
            return None

        # /start itself asks for the consent
        if event.text and event.text.startswith("/start"):
            return await handler(event, data)

        if not await has_consent(data["pool"], event.from_user.id):
            # A short reminder only: a new button on every message would leave many old buttons
            await event.answer(texts.CONSENT_REQUIRED)
            return None

        return await handler(event, data)
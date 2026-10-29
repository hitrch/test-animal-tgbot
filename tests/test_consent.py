"""Consent: only private chats, and nothing but /start until "Погоджуюсь" is pressed."""
from unittest.mock import AsyncMock

import pytest

from bot import middlewares, texts
from bot.handlers import consent
from bot.middlewares import AccessMiddleware
from tests.fakes import HEDGEHOG, make_callback, make_message, replies


@pytest.fixture
def consent_given(monkeypatch):
    """Set .return_value to True for a user who already agreed."""
    has_consent = AsyncMock(return_value=False)
    monkeypatch.setattr(middlewares, "has_consent", has_consent)
    monkeypatch.setattr(consent, "has_consent", has_consent)
    return has_consent


async def pass_through_middleware(message):
    handler = AsyncMock(return_value="handled")
    result = await AccessMiddleware()(handler, message, {"pool": None})
    return handler, result


# --- middleware ---

async def test_group_messages_are_ignored_silently(consent_given):
    consent_given.return_value = True
    message = make_message("скільки внесок?", chat_type="group")

    handler, _ = await pass_through_middleware(message)

    handler.assert_not_called()
    message.answer.assert_not_called()


async def test_without_consent_the_bot_only_reminds_without_a_new_button(consent_given):
    message = make_message("скільки внесок?")

    handler, _ = await pass_through_middleware(message)

    handler.assert_not_called()
    assert replies(message) == [texts.CONSENT_REQUIRED]
    assert "reply_markup" not in message.answer.call_args.kwargs


async def test_without_consent_documents_are_not_accepted_either(consent_given):
    message = make_message(None, document=object())

    handler, _ = await pass_through_middleware(message)

    handler.assert_not_called()


async def test_start_works_without_consent(consent_given):
    handler, result = await pass_through_middleware(make_message("/start"))

    assert result == "handled"


async def test_with_consent_messages_reach_the_handlers(consent_given):
    consent_given.return_value = True

    handler, result = await pass_through_middleware(make_message("скільки внесок?"))

    assert result == "handled"
    consent_given.assert_awaited_once_with(None, HEDGEHOG)


# --- /start and the button ---

async def test_start_shows_the_rules_with_the_button(consent_given):
    message = make_message("/start")

    await consent.start(message, pool=None)

    assert replies(message) == [texts.CONSENT_REQUEST]
    markup = message.answer.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].text == texts.CONSENT_BUTTON


async def test_start_after_consent_shows_how_to_use(consent_given):
    consent_given.return_value = True
    message = make_message("/start")

    await consent.start(message, pool=None)

    assert replies(message) == [texts.HOW_TO_USE]


async def test_pressing_the_button_saves_consent_and_removes_the_button(monkeypatch):
    add_consent = AsyncMock()
    monkeypatch.setattr(consent, "add_consent", add_consent)
    callback = make_callback()

    await consent.agree(callback, pool=None)

    add_consent.assert_awaited_once_with(None, HEDGEHOG)
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert replies(callback.message) == [texts.CONSENT_THANKS]
    callback.answer.assert_awaited_once()
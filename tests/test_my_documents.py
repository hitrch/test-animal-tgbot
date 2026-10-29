"""/my: the user's own documents and deleting them."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot import texts
from bot.handlers import my_documents
from bot.handlers.my_documents import DeleteChoice
from tests.fakes import HEDGEHOG, button_texts, make_callback, make_message, replies


@pytest.fixture
def db(monkeypatch):
    """Database functions used by /my, replaced by mocks (the SQL is in test_db.py)."""
    db = SimpleNamespace(
        user_documents=AsyncMock(return_value=[]),
        get_own_document=AsyncMock(return_value={"id": 5, "title": "Внески 2026"}),
        delete_document=AsyncMock(return_value=(True, None)),
    )
    for name, mock in vars(db).items():
        monkeypatch.setattr(my_documents, name, mock)
    return db


def own_document(document_id, title, *, replaced_by=None):
    return {
        "id": document_id,
        "title": title,
        "place": "Під дубом",
        "uploaded_at": datetime(2026, 10, 28, 12, 0),
        "is_active": replaced_by is None,
        "replaced_by_title": replaced_by,
    }


async def test_user_without_documents_is_told_how_to_add_one(db):
    message = make_message("/my")

    await my_documents.my_documents(message, pool=None)

    assert replies(message) == [texts.MY_EMPTY]


async def test_list_shows_replaced_documents_and_a_delete_button_for_each(db):
    db.user_documents.return_value = [
        own_document(6, "Внески 2026"),
        own_document(5, "Внески 2025", replaced_by="Внески від Лиса"),
    ]
    message = make_message("/my")

    await my_documents.my_documents(message, pool=None)

    db.user_documents.assert_awaited_once_with(None, HEDGEHOG)
    reply = replies(message)[0]
    assert texts.MY_DOCUMENT.format(title="Внески 2026", place="Під дубом", date="28.10.2026") in reply
    assert texts.MY_REPLACED.format(title="Внески від Лиса") in reply
    assert button_texts(message.answer.call_args.kwargs["reply_markup"]) == [
        texts.DELETE_BUTTON.format(title="Внески 2026"),
        texts.DELETE_BUTTON.format(title="Внески 2025"),
    ]


async def test_delete_button_asks_for_confirmation(db):
    callback = make_callback()

    await my_documents.ask_delete(callback, callback_data=DeleteChoice(document_id=5, step="ask"), pool=None)

    db.get_own_document.assert_awaited_once_with(None, 5, HEDGEHOG)
    assert replies(callback.message) == [texts.CONFIRM_DELETE.format(title="Внески 2026")]
    db.delete_document.assert_not_called()


async def test_delete_button_of_someone_elses_document_is_refused(db):
    db.get_own_document.return_value = None
    callback = make_callback()

    await my_documents.ask_delete(callback, callback_data=DeleteChoice(document_id=5, step="ask"), pool=None)

    callback.answer.assert_awaited_once_with(texts.NOT_YOUR_DOCUMENT, show_alert=True)


async def test_confirmed_delete_names_the_old_version_that_works_again(db):
    db.delete_document.return_value = (True, "Внески 2025")
    callback = make_callback()

    await my_documents.confirm_delete(callback, callback_data=DeleteChoice(document_id=5, step="yes"), pool=None)

    db.delete_document.assert_awaited_once_with(None, 5, HEDGEHOG)
    callback.message.edit_text.assert_awaited_once_with(
        texts.DELETED.format(title="Внески 2026") + "\n" + texts.RESTORED.format(title="Внески 2025")
    )


async def test_confirmed_delete_of_someone_elses_document_deletes_nothing(db):
    db.get_own_document.return_value = None
    callback = make_callback()

    await my_documents.confirm_delete(callback, callback_data=DeleteChoice(document_id=5, step="yes"), pool=None)

    db.delete_document.assert_not_called()
    callback.message.edit_text.assert_awaited_once_with(texts.NOT_YOUR_DOCUMENT)


async def test_no_button_cancels_the_delete(db):
    callback = make_callback()

    await my_documents.cancel_delete(callback)

    callback.message.edit_text.assert_awaited_once_with(texts.DELETE_CANCELLED)
    db.delete_document.assert_not_called()
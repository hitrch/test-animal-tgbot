"""Uploading documents: checks, place and topics of the parts, new version or a separate document."""
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest

from bot import texts
from bot.chunks import Chunk
from bot.handlers import documents
from bot.handlers.documents import MAX_FILE_SIZE, PlaceChoice, VersionChoice, document_place
from bot.llm import MIXED_PLACES, LLMUnavailableError, ParsedChunk
from bot.places import ALL_PLACES
from tests.fakes import HEDGEHOG, NoChatAction, button_texts, make_callback, make_message, make_state, replies

FEES_TEXT = "Для мешканців рукавички «Під дубом»\nВнесок — 4 жолуді до першого снігу.".encode()
FEES_HASH = hashlib.sha256(FEES_TEXT).hexdigest()
FEES_PARSED = ParsedChunk(place="Під дубом", topics=["внески"])


# --- document_place ---

@pytest.mark.parametrize(("chunk_places", "expected"), [
    (["Під дубом"], "Під дубом"),
    # Later parts usually do not repeat the place: the one named place is the document's place
    (["листяний", None, None], "листяний"),
    (["Під дубом", "Під дубом"], "Під дубом"),
    # Different places in one document: the user is asked to split it
    (["Під дубом", "листяний"], MIXED_PLACES),
    (["Під дубом", MIXED_PLACES], MIXED_PLACES),
    # No place anywhere: the bot asks the user with buttons
    ([None, None], None),
])
def test_document_place_from_places_of_its_parts(chunk_places, expected):
    assert document_place(chunk_places) == expected


# --- receiving a file ---

@pytest.fixture
def db(monkeypatch):
    """Database functions used by the upload handlers, replaced by mocks (the SQL is in test_db.py)."""
    db = SimpleNamespace(
        document_exists=AsyncMock(return_value=False),
        find_similar_documents=AsyncMock(return_value=[]),
        add_document=AsyncMock(return_value=10),
    )
    for name, mock in vars(db).items():
        monkeypatch.setattr(documents, name, mock)
    monkeypatch.setattr(documents, "ChatActionSender", NoChatAction)
    return db


@pytest.fixture
def llm():
    return SimpleNamespace(parse_chunk=AsyncMock(return_value=FEES_PARSED))


@pytest.fixture
def state():
    return make_state()


def file_message(name="Внески.txt", data=FEES_TEXT, size=None):
    document = SimpleNamespace(file_name=name, file_size=len(data) if size is None else size, file_id="telegram-file")
    message = make_message(document=document)
    message.bot.download.return_value = io.BytesIO(data)
    return message


async def upload(message, state, llm):
    await documents.receive_document(message, state=state, pool=None, llm=llm)


@pytest.mark.parametrize(("name", "reply"), [
    ("Внески.doc", texts.OLD_WORD_FORMAT),
    ("Внески.DOC", texts.OLD_WORD_FORMAT),
    ("Фото.jpg", texts.UNSUPPORTED_FORMAT),
    ("Таблиця.xlsx", texts.UNSUPPORTED_FORMAT),
    ("Без розширення", texts.UNSUPPORTED_FORMAT),
])
async def test_unsupported_formats_are_refused_before_downloading(db, llm, state, name, reply):
    message = file_message(name)

    await upload(message, state, llm)

    assert replies(message) == [reply]
    message.bot.download.assert_not_called()


async def test_file_over_20_mb_is_refused_before_downloading(db, llm, state):
    message = file_message(size=MAX_FILE_SIZE + 1)

    await upload(message, state, llm)

    assert replies(message) == [texts.FILE_TOO_BIG]
    message.bot.download.assert_not_called()


async def test_file_of_exactly_20_mb_is_accepted(db, llm, state):
    message = file_message(size=MAX_FILE_SIZE)

    await upload(message, state, llm)

    message.bot.download.assert_awaited_once()


async def test_photo_gets_the_list_of_formats():
    message = make_message()

    await documents.receive_photo(message)

    assert replies(message) == [texts.UNSUPPORTED_FORMAT]


async def test_same_file_again_is_not_saved_twice(db, llm, state):
    db.document_exists.return_value = True
    message = file_message()

    await upload(message, state, llm)

    db.document_exists.assert_awaited_once_with(None, FEES_HASH)
    assert replies(message) == [texts.DUPLICATE]
    llm.parse_chunk.assert_not_called()


@pytest.mark.parametrize(("name", "data"), [
    ("Порожній.txt", b"  \n  "),
    ("Зламаний.pdf", b"this is not a pdf"),
    ("Зламаний.docx", b"this is not a docx"),
])
async def test_empty_or_broken_file_cannot_be_read(db, llm, state, name, data):
    message = file_message(name, data)

    await upload(message, state, llm)

    assert replies(message) == [texts.CANNOT_READ]
    db.add_document.assert_not_called()


async def test_document_with_a_clear_place_and_no_similar_ones_is_saved_right_away(db, llm, state):
    message = file_message()

    await upload(message, state, llm)

    db.add_document.assert_awaited_once_with(
        None,
        title="Внески",
        place="Під дубом",
        chunks=[Chunk(content=FEES_TEXT.decode(), topics=["внески"])],
        file_hash=FEES_HASH,
        telegram_file_id="telegram-file",
        uploader_id=HEDGEHOG,
        uploader_name="Їжак",
        replaces=None,
    )
    saved = texts.SAVED.format(title="Внески", place="Під дубом", topics="внески")
    # The reminder that every animal will see the document
    assert replies(message) == [f"{saved}\n\n{texts.VISIBLE_TO_ALL}"]
    assert await state.get_data() == {}


async def test_long_document_is_parsed_part_by_part(db, llm, state):
    line = "Порада: шукай гриби після дощу біля старого пня, але не чіпай мухоморів. " * 2
    message = file_message(data="\n".join([line] * 30).encode())

    await upload(message, state, llm)

    assert replies(message)[0] == texts.LONG_DOCUMENT.format(parts=2)
    numbers = [call.args[1:3] for call in llm.parse_chunk.call_args_list]
    assert numbers == [(1, 2), (2, 2)]
    assert len(db.add_document.call_args.kwargs["chunks"]) == 2


async def test_document_for_different_places_is_not_accepted(db, llm, state):
    llm.parse_chunk.return_value = ParsedChunk(place=MIXED_PLACES, topics=["внески"])
    message = file_message()

    await upload(message, state, llm)

    assert replies(message) == [texts.MIXED_PLACES]
    db.add_document.assert_not_called()


async def test_groq_failure_in_the_middle_saves_nothing(db, llm, state):
    llm.parse_chunk.side_effect = [FEES_PARSED, LLMUnavailableError()]
    message = file_message(data=("Внесок — 4 жолуді. " * 200).encode())

    await upload(message, state, llm)

    assert replies(message)[-1] == texts.TRY_LATER
    db.add_document.assert_not_called()
    assert await state.get_data() == {}


# --- unclear place: buttons with places ---

async def test_unclear_place_is_asked_with_a_button_for_every_place(db, llm, state):
    llm.parse_chunk.return_value = ParsedChunk(place=None, topics=["внески"])
    message = file_message()

    await upload(message, state, llm)

    assert replies(message) == [texts.ASK_DOCUMENT_PLACE.format(title="Внески")]
    assert button_texts(message.answer.call_args.kwargs["reply_markup"]) == ALL_PLACES
    db.add_document.assert_not_called()


async def test_chosen_place_is_used_for_the_document(db, llm, state):
    llm.parse_chunk.return_value = ParsedChunk(place=None, topics=["внески"])
    await upload(file_message(), state, llm)
    callback = make_callback()
    callback.message.text = texts.ASK_DOCUMENT_PLACE.format(title="Внески")

    choice = PlaceChoice(upload=FEES_HASH[:16], index=ALL_PLACES.index("У ліщині"))
    await documents.choose_place(callback, callback_data=choice, state=state, pool=None)

    assert db.add_document.call_args.kwargs["place"] == "У ліщині"
    # The buttons are removed and the choice is written into the question
    assert callback.message.edit_text.call_args.args[0].endswith(texts.PLACE_CHOSEN.format(place="У ліщині"))


# --- similar documents: new version or separate ---

SIMILAR = [{"id": 7, "title": "Статут", "topics": ["внески", "керівництво"]}]


async def test_similar_document_of_the_place_is_shown_with_topics_that_would_be_lost(db, llm, state):
    db.find_similar_documents.return_value = SIMILAR
    message = file_message()

    await upload(message, state, llm)

    db.find_similar_documents.assert_awaited_once_with(None, "Під дубом", ["внески"])
    reply = replies(message)[0]
    assert texts.SIMILAR_DOCUMENT.format(title="Статут", topics="внески, керівництво") in reply
    assert texts.LOST_TOPICS.format(topics="керівництво") in reply
    assert button_texts(message.answer.call_args.kwargs["reply_markup"]) == [
        texts.NEW_VERSION_BUTTON.format(title="Статут"),
        texts.SEPARATE_BUTTON,
    ]
    db.add_document.assert_not_called()


async def press_version_button(state, replaces, upload_id=FEES_HASH[:16]):
    callback = make_callback()
    choice = VersionChoice(upload=upload_id, replaces=replaces)
    await documents.choose_version(callback, callback_data=choice, state=state, pool=None)
    return callback


async def test_new_version_button_replaces_the_old_document(db, llm, state):
    db.find_similar_documents.return_value = SIMILAR
    await upload(file_message(), state, llm)

    callback = await press_version_button(state, replaces=7)

    assert db.add_document.call_args.kwargs["replaces"] == 7
    assert texts.REPLACED.format(title="Статут") in replies(callback.message)[0]
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)


async def test_separate_document_button_keeps_both(db, llm, state):
    db.find_similar_documents.return_value = SIMILAR
    await upload(file_message(), state, llm)

    callback = await press_version_button(state, replaces=0)

    assert db.add_document.call_args.kwargs["replaces"] is None
    assert "замінив" not in replies(callback.message)[0]


async def test_button_of_an_older_upload_does_nothing(db, llm, state):
    db.find_similar_documents.return_value = SIMILAR
    await upload(file_message(), state, llm)

    callback = await press_version_button(state, replaces=7, upload_id="older-upload")

    callback.answer.assert_awaited_once_with(texts.UPLOAD_EXPIRED, show_alert=True)
    db.add_document.assert_not_called()


async def test_same_file_saved_while_waiting_for_the_button_is_a_duplicate(db, llm, state):
    db.find_similar_documents.return_value = SIMILAR
    db.add_document.side_effect = asyncpg.UniqueViolationError("duplicate")
    await upload(file_message(), state, llm)

    callback = await press_version_button(state, replaces=0)

    assert replies(callback.message) == [texts.DUPLICATE]

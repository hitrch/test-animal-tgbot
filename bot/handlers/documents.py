"""Uploading documents: checks, place and topics of each part, new version or a separate document."""
import asyncio
import hashlib
import logging
from pathlib import Path

import asyncpg
from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from bot import texts
from bot.chunks import Chunk, all_topics, split_text
from bot.db import add_document, document_exists, find_similar_documents
from bot.extract import SUPPORTED_EXTENSIONS, extract_text
from bot.llm import LLM, MIXED_PLACES, LLMUnavailableError
from bot.places import ALL_PLACES

logger = logging.getLogger(__name__)

# Bots cannot download bigger files from Telegram
MAX_FILE_SIZE = 20 * 1024 * 1024

router = Router()


class PlaceChoice(CallbackData, prefix="place"):
    upload: str  # which upload the button belongs to
    index: int  # position in ALL_PLACES: Cyrillic names may not fit into 64 bytes of button data


class VersionChoice(CallbackData, prefix="version"):
    upload: str
    replaces: int  # id of the document to replace; 0 means a separate document


def document_place(chunk_places: list[str | None]) -> str | None:
    """One place for the whole document from the places of its parts.

    MIXED_PLACES if the parts name different places, None if no part names a place.
    """
    named = {place for place in chunk_places if place is not None}
    if MIXED_PLACES in named or len(named) > 1:
        return MIXED_PLACES
    return named.pop() if named else None


@router.message(F.document)
async def receive_document(message: Message, state: FSMContext, pool: asyncpg.Pool, llm: LLM) -> None:
    document = message.document
    filename = document.file_name or ""
    extension = Path(filename).suffix.lower()
    user_id = message.from_user.id

    if extension == ".doc":
        await message.answer(texts.OLD_WORD_FORMAT)
        return
    if extension not in SUPPORTED_EXTENSIONS:
        await message.answer(texts.UNSUPPORTED_FORMAT)
        return
    if document.file_size and document.file_size > MAX_FILE_SIZE:
        await message.answer(texts.FILE_TOO_BIG)
        return

    # Keep showing "typing..." while the file is downloaded, read and parsed
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        file = await message.bot.download(document)
        data = file.read()
        file_hash = hashlib.sha256(data).hexdigest()
        if await document_exists(pool, file_hash):
            logger.info("Upload from %s: duplicate file", user_id)
            await message.answer(texts.DUPLICATE)
            return

        try:
            # Text extraction (OCR for scans) is slow and blocking, so run it in a separate thread
            text = await asyncio.to_thread(extract_text, filename, data)
        except Exception:
            # pypdf and python-docx raise many different errors for broken or protected files
            logger.exception("Upload from %s: cannot read the file", user_id)
            text = ""
        if not text:
            await message.answer(texts.CANNOT_READ)
            return

        title = Path(filename).stem
        parts = split_text(text)
        if len(parts) > 1:
            await message.answer(texts.LONG_DOCUMENT.format(parts=len(parts)))

        chunks = []
        places = []
        try:
            for number, part in enumerate(parts, start=1):
                parsed = await llm.parse_chunk(title, number, len(parts), part)
                chunks.append(Chunk(content=part, topics=parsed.topics))
                places.append(parsed.place)
        except LLMUnavailableError:
            # Nothing is saved: half a document is worse than none
            await message.answer(texts.TRY_LATER)
            return

    place = document_place(places)
    if place == MIXED_PLACES:
        logger.info("Upload from %s: advice for different places", user_id)
        await message.answer(texts.MIXED_PLACES)
        return

    # The upload waits here for the user's buttons. FSM data lives in memory (aiogram's MemoryStorage),
    # one per user, so a restart of the bot forgets it and the user is asked to send the file again
    upload = file_hash[:16]
    await state.set_data({
        "upload": upload,
        "title": title,
        "place": place,
        "chunks": chunks,
        "file_hash": file_hash,
        "telegram_file_id": document.file_id,
        "uploader_id": user_id,
        "uploader_name": message.from_user.full_name,
    })

    if place is None:
        await message.answer(texts.ASK_DOCUMENT_PLACE.format(title=title), reply_markup=_place_keyboard(upload))
        return
    await _ask_version_or_save(message, state, pool)


@router.message(F.photo)
async def receive_photo(message: Message) -> None:
    await message.answer(texts.UNSUPPORTED_FORMAT)


@router.callback_query(PlaceChoice.filter())
async def choose_place(
    callback: CallbackQuery, callback_data: PlaceChoice, state: FSMContext, pool: asyncpg.Pool
) -> None:
    if not await _is_current(callback, callback_data.upload, state):
        return
    place = ALL_PLACES[callback_data.index]
    await state.update_data(place=place)
    # Editing the text without reply_markup also removes the buttons, so they cannot be pressed again
    await callback.message.edit_text(f"{callback.message.text}\n\n{texts.PLACE_CHOSEN.format(place=place)}")
    await callback.answer()
    await _ask_version_or_save(callback.message, state, pool)


@router.callback_query(VersionChoice.filter())
async def choose_version(
    callback: CallbackQuery, callback_data: VersionChoice, state: FSMContext, pool: asyncpg.Pool
) -> None:
    if not await _is_current(callback, callback_data.upload, state):
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _save(callback.message, state, pool, replaces=callback_data.replaces or None)


async def _is_current(callback: CallbackQuery, upload: str, state: FSMContext) -> bool:
    """False if the button belongs to an older upload or the bot was restarted since."""
    data = await state.get_data()
    if data.get("upload") == upload:
        return True
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(texts.UPLOAD_EXPIRED, show_alert=True)
    return False


async def _ask_version_or_save(message: Message, state: FSMContext, pool: asyncpg.Pool) -> None:
    """The place is known: ask about similar documents of this place, or save right away if there are none."""
    data = await state.get_data()
    topics = all_topics(data["chunks"])
    similar = await find_similar_documents(pool, data["place"], topics)
    if not similar:
        await _save(message, state, pool, replaces=None)
        return

    # Titles are needed later to say which document was replaced
    await state.update_data(similar_titles={row["id"]: row["title"] for row in similar})
    lines = [texts.ASK_VERSION.format(place=data["place"])]
    for row in similar:
        line = texts.SIMILAR_DOCUMENT.format(title=row["title"], topics=", ".join(row["topics"]))
        # The user decides, but should see which advice a replacement would take away
        lost = [topic for topic in row["topics"] if topic not in topics]
        if lost:
            line += texts.LOST_TOPICS.format(topics=", ".join(lost))
        lines.append(line)
    lines += ["", texts.ASK_VERSION_QUESTION.format(title=data["title"], topics=", ".join(topics))]
    await message.answer("\n".join(lines), reply_markup=_version_keyboard(data["upload"], similar))


async def _save(message: Message, state: FSMContext, pool: asyncpg.Pool, replaces: int | None) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        document_id = await add_document(
            pool,
            title=data["title"],
            place=data["place"],
            chunks=data["chunks"],
            file_hash=data["file_hash"],
            telegram_file_id=data["telegram_file_id"],
            uploader_id=data["uploader_id"],
            uploader_name=data["uploader_name"],
            replaces=replaces,
        )
    except asyncpg.UniqueViolationError:
        # The same file was saved while this upload was waiting for a button
        await message.answer(texts.DUPLICATE)
        return
    logger.info(
        "Upload from %s: saved document %s, place %s, %d parts, replaces %s",
        data["uploader_id"], document_id, data["place"], len(data["chunks"]), replaces,
    )

    topics = ", ".join(all_topics(data["chunks"]))
    lines = [texts.SAVED.format(title=data["title"], place=data["place"], topics=topics)]
    if replaces is not None:
        lines.append(texts.REPLACED.format(title=data["similar_titles"][replaces]))
    lines += ["", texts.VISIBLE_TO_ALL]
    await message.answer("\n".join(lines))


def _place_keyboard(upload: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=place, callback_data=PlaceChoice(upload=upload, index=index).pack())
        for index, place in enumerate(ALL_PLACES)
    ]
    # Two buttons per row
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _version_keyboard(upload: str, similar: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows = []
    # One button per row with the title of the document it replaces
    for row in similar:
        text = texts.NEW_VERSION_BUTTON.format(title=row["title"])
        new_version = VersionChoice(upload=upload, replaces=row["id"]).pack()
        rows.append([InlineKeyboardButton(text=text, callback_data=new_version)])
    separate = VersionChoice(upload=upload, replaces=0).pack()
    rows.append([InlineKeyboardButton(text=texts.SEPARATE_BUTTON, callback_data=separate)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
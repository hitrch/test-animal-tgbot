"""/my: the user's own documents and deleting them."""
import logging

import asyncpg
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import texts
from bot.db import delete_document, get_own_document, user_documents

logger = logging.getLogger(__name__)

router = Router()


class DeleteChoice(CallbackData, prefix="delete"):
    document_id: int
    step: str  # "ask": show the confirmation; "yes" / "no": the answer to it


@router.message(Command("my"))
async def my_documents(message: Message, pool: asyncpg.Pool) -> None:
    documents = await user_documents(pool, message.from_user.id)
    if not documents:
        await message.answer(texts.MY_EMPTY)
        return

    lines = [texts.MY_HEADER, ""]
    buttons = []
    for document in documents:
        date = document["uploaded_at"].strftime("%d.%m.%Y")
        lines.append(texts.MY_DOCUMENT.format(title=document["title"], place=document["place"], date=date))
        if not document["is_active"]:
            lines.append(texts.MY_REPLACED.format(title=document["replaced_by_title"]))
        ask = DeleteChoice(document_id=document["id"], step="ask").pack()
        buttons.append([InlineKeyboardButton(text=texts.DELETE_BUTTON.format(title=document["title"]), callback_data=ask)])
    lines += ["", texts.MY_FOOTER]
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(DeleteChoice.filter(F.step == "ask"))
async def ask_delete(callback: CallbackQuery, callback_data: DeleteChoice, pool: asyncpg.Pool) -> None:
    # Button data comes from the user's app, so ownership is checked in the database every time
    document = await get_own_document(pool, callback_data.document_id, callback.from_user.id)
    if document is None:
        await callback.answer(texts.NOT_YOUR_DOCUMENT, show_alert=True)
        return

    yes = DeleteChoice(document_id=document["id"], step="yes").pack()
    no = DeleteChoice(document_id=document["id"], step="no").pack()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.YES_DELETE_BUTTON, callback_data=yes),
        InlineKeyboardButton(text=texts.NO_DELETE_BUTTON, callback_data=no),
    ]])
    await callback.message.answer(texts.CONFIRM_DELETE.format(title=document["title"]), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(DeleteChoice.filter(F.step == "no"))
async def cancel_delete(callback: CallbackQuery) -> None:
    # Editing the text without reply_markup also removes the buttons
    await callback.message.edit_text(texts.DELETE_CANCELLED)
    await callback.answer()


@router.callback_query(DeleteChoice.filter(F.step == "yes"))
async def confirm_delete(callback: CallbackQuery, callback_data: DeleteChoice, pool: asyncpg.Pool) -> None:
    user_id = callback.from_user.id
    # The title is needed for the reply, and the document will be gone after deleting
    document = await get_own_document(pool, callback_data.document_id, user_id)
    deleted, restored_title = (False, None)
    if document is not None:
        deleted, restored_title = await delete_document(pool, callback_data.document_id, user_id)

    if not deleted:
        await callback.message.edit_text(texts.NOT_YOUR_DOCUMENT)
        await callback.answer()
        return

    logger.info("User %s deleted document %s, restored: %s", user_id, callback_data.document_id, restored_title)
    lines = [texts.DELETED.format(title=document["title"])]
    if restored_title is not None:
        lines.append(texts.RESTORED.format(title=restored_title))
    await callback.message.edit_text("\n".join(lines))
    await callback.answer()
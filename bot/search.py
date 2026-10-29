"""Answer a question: parse it, find documents by place and topics, ask the LLM, add sources."""
from collections.abc import Mapping, Sequence
from datetime import date

import asyncpg

from bot import texts
from bot.chunks import select_chunks
from bot.db import fetch_chunks, find_documents, places_with_documents
from bot.llm import LLM, UNKNOWN_PLACE, LLMUnavailableError
from bot.places import ALL_PLACES, WHOLE_FOREST, place_chain


async def answer_question(question: str, pool: asyncpg.Pool, llm: LLM, today: date) -> str:
    """Return the text to send back to the user."""
    try:
        parsed = await llm.parse_question(question)

        if parsed.place == UNKNOWN_PLACE:
            if parsed.options:
                options = " чи ".join(f"«{option}»" for option in parsed.options)
                return texts.ASK_WHICH_PLACE.format(options=options)
            return await _ask_place(pool, parsed.topics)

        if parsed.place is None:
            # No place named: general advice for the whole forest, if there is any
            documents = await find_documents(pool, [WHOLE_FOREST], parsed.topics)
            if not documents:
                return await _ask_place(pool, parsed.topics)
            answer, found = await _answer_from_documents(
                question, [WHOLE_FOREST], documents, parsed.topics, pool, llm, today
            )
            # The reminder about local rules only makes sense next to real advice
            return f"{answer}\n\n{texts.LOCAL_RULES_NOTE}" if found else answer

        chain = place_chain(parsed.place)
        documents = await find_documents(pool, chain, parsed.topics)
        if not documents:
            return texts.NOT_FOUND
        answer, _ = await _answer_from_documents(question, chain, documents, parsed.topics, pool, llm, today)
        return answer

    except LLMUnavailableError:
        return texts.TRY_LATER


async def _answer_from_documents(
    question: str,
    chain: list[str],
    found_documents: Sequence[Mapping],
    topics: list[str],
    pool: asyncpg.Pool,
    llm: LLM,
    today: date,
) -> tuple[str, bool]:
    """Return the reply text and whether the answer was found in the documents."""
    # Only the parts on the question's topics go to the LLM, within the token budget
    chunks = await fetch_chunks(pool, [document["id"] for document in found_documents])
    documents = select_chunks(found_documents, chunks, chain, question, topics)
    if not documents:
        return texts.NOT_FOUND, False

    answer = await llm.answer_question(question, chain, documents, today)
    # No documents used means the answer is not in them: never show a source then
    if not answer.used_documents:
        return answer.text or texts.NOT_FOUND, False

    lines = [answer.text]
    # Chain of a mitten in two forest parts: mitten, part, part, whole forest
    if answer.parts_differ and len(chain) == 4:
        lines += ["", texts.PARTS_DIFFER.format(place=chain[0])]
    sources = [format_source(documents[i]) for i in answer.used_documents]
    lines += ["", texts.SOURCES_HEADER, *sources]
    return "\n".join(lines), True


def format_source(document: Mapping) -> str:
    """Source line built from database fields, so the LLM cannot distort it."""
    if document["archive_path"]:
        return texts.SOURCE_ARCHIVE.format(title=document["title"], place=document["place"])
    return texts.SOURCE_UPLOADED.format(
        title=document["title"],
        place=document["place"],
        name=document["uploader_name"],
        date=document["uploaded_at"].strftime("%d.%m.%Y"),
    )


async def _ask_place(pool: asyncpg.Pool, topics: list[str]) -> str:
    places = await places_with_documents(pool, topics)
    if not places:
        return texts.NOT_FOUND
    # Show places in map order: whole forest, forest parts, mittens
    ordered = sorted(places, key=ALL_PLACES.index)
    return texts.ASK_PLACE.format(places=", ".join(ordered))
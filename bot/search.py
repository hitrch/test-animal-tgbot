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
            reply = await _find_answer(question, [WHOLE_FOREST], parsed.topics, pool, llm, today)
            if reply is not None:
                return f"{reply}\n\n{texts.LOCAL_RULES_NOTE}"
            # Asking for a place helps only if the question has a topic: then the user sees which
            # places have advice on it. No topic means the question is not about the forest at all.
            # The whole forest was just searched, so it is not offered again
            if not parsed.topics:
                return texts.NOT_FOUND
            return await _ask_place(pool, parsed.topics, skip=WHOLE_FOREST)

        chain = place_chain(parsed.place)
        reply = await _find_answer(question, chain, parsed.topics, pool, llm, today)
        return reply or texts.NOT_FOUND

    except LLMUnavailableError:
        return texts.TRY_LATER


async def _find_answer(
    question: str, chain: list[str], topics: list[str], pool: asyncpg.Pool, llm: LLM, today: date
) -> str | None:
    """Reply with sources from the active documents of these places, or None if the answer is not there."""
    sent = set()  # (document id, part position) already shown to the LLM

    # First try: only the parts on the question's topics
    found = await find_documents(pool, chain, topics)
    if found:
        chunks = await fetch_chunks(pool, [document["id"] for document in found])
        documents = select_chunks(found, chunks, chain, question, topics)
        reply = await _ask_llm(question, chain, documents, llm, today)
        if reply is not None:
            return reply
        sent = {(document["id"], position) for document in documents for position in document["positions"]}

    # Second try: the question's topics may be tagged wrong, so look through every document
    # of these places and choose the parts by the words of the question
    everything = await find_documents(pool, chain, [])
    chunks = await fetch_chunks(pool, [document["id"] for document in everything])
    chunks = [chunk for chunk in chunks if (chunk["document_id"], chunk["position"]) not in sent]
    documents = select_chunks(everything, chunks, chain, question, [], require_shared_words=True)
    return await _ask_llm(question, chain, documents, llm, today)


async def _ask_llm(
    question: str, chain: list[str], documents: Sequence[Mapping], llm: LLM, today: date
) -> str | None:
    """Reply text with sources, or None if there are no documents or the answer is not in them."""
    if not documents:
        return None
    answer = await llm.answer_question(question, chain, documents, today)
    # No documents used means the answer is not in them: never show a source then
    if not answer.used_documents:
        return None

    lines = [answer.text]
    # Chain of a mitten in two forest parts: mitten, part, part, whole forest
    if answer.parts_differ and len(chain) == 4:
        lines += ["", texts.PARTS_DIFFER.format(place=chain[0])]
    sources = [format_source(documents[i]) for i in answer.used_documents]
    lines += ["", texts.SOURCES_HEADER, *sources]
    return "\n".join(lines)


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


async def _ask_place(pool: asyncpg.Pool, topics: list[str], skip: str | None = None) -> str:
    """Ask the user to name a place, listing the places that have advice on these topics."""
    places = [place for place in await places_with_documents(pool, topics) if place != skip]
    if not places:
        return texts.NOT_FOUND
    # Show places in map order: whole forest, forest parts, mittens
    ordered = sorted(places, key=ALL_PLACES.index)
    return texts.ASK_PLACE.format(places=", ".join(ordered))
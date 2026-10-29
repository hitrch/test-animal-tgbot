"""Split document text into parts and choose the parts to send to the LLM.

A long guide does not fit into one Groq request (8K tokens per minute),
so documents are stored in parts and an answer gets only the parts it needs.
Measured on the archive: Ukrainian text is about 2.65 characters per token.
"""
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bot.topics import TOPICS

# About 1.1K tokens: a part plus the prompt is a small request when a document is uploaded
CHUNK_SIZE = 3000
# About 2.3K tokens of document text per answer: the whole request is ~3.2K,
# so two questions still fit into one minute of the 8K limit
ANSWER_BUDGET = 6000


@dataclass
class Chunk:
    content: str
    topics: list[str]


def all_topics(chunks: Sequence[Chunk]) -> list[str]:
    """Topics of the whole document: every topic of its parts, in the order of the topic list."""
    return [topic for topic in TOPICS if any(topic in chunk.topics for chunk in chunks)]


def split_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Split text into parts of at most `size` characters, at line borders where possible."""
    parts = []
    current = ""
    for line in _lines(text, size):
        if current and len(current) + 1 + len(line) > size:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts


def _lines(text: str, size: int):
    """Non-empty lines of the text; a line longer than `size` is cut at spaces."""
    for line in text.splitlines():
        line = line.strip()
        while len(line) > size:
            cut = line.rfind(" ", 0, size)
            if cut <= 0:  # no space at all: cut right at the limit
                cut = size
            yield line[:cut]
            line = line[cut:].strip()
        if line:
            yield line


def select_chunks(
    documents: Sequence[Mapping],
    chunks: Sequence[Mapping],
    chain: Sequence[str],
    question: str,
    topics: Sequence[str],
    budget: int = ANSWER_BUDGET,
) -> list[dict]:
    """Choose the parts that fit into the budget and glue them back into documents.

    documents: rows with "id" and "place"; chunks: rows with "document_id", "position", "content", "topics".
    Returns copies of the documents with "content" made of the chosen parts, in the original order.
    Documents without chosen parts are left out.
    """
    place_of = {document["id"]: document["place"] for document in documents}
    question_words = _stems(question)

    candidates = [
        chunk for chunk in chunks
        if chunk["document_id"] in place_of and (not topics or set(chunk["topics"]) & set(topics))
    ]
    # The most specific place first; within a place, parts sharing more words with the question
    candidates.sort(key=lambda chunk: (
        chain.index(place_of[chunk["document_id"]]),
        -len(question_words & _stems(chunk["content"])),
    ))

    chosen = []
    used = 0
    for chunk in candidates:
        # Always take the best part; skip the ones that do not fit, a smaller one may still fit
        if chosen and used + len(chunk["content"]) > budget:
            continue
        chosen.append(chunk)
        used += len(chunk["content"])

    result = []
    for document in documents:
        parts = sorted((c for c in chosen if c["document_id"] == document["id"]), key=lambda c: c["position"])
        if parts:
            result.append({**document, "content": "\n\n".join(part["content"] for part in parts)})
    return result


def _stems(text: str) -> set[str]:
    """First 4 letters of each word: "грибів" and "гриби" both give "гриб", so word forms still match."""
    return {word[:4] for word in re.findall(r"\w+", text.lower()) if len(word) >= 4}
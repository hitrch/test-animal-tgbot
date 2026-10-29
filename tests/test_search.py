"""Answering a question with a fake database and a fake LLM.

The fake database filters like the SQL in bot/db.py (active documents, place, topics), so these
tests check which documents reach the LLM and what the bot itself adds to the reply. The SQL
itself is tested on a real Postgres in test_db.py.
"""
from datetime import date, datetime

import pytest

from bot import search, texts
from bot.llm import UNKNOWN_PLACE, Answer, LLMUnavailableError, ParsedQuestion
from bot.search import answer_question, format_source

QUESTION_DATE = date(2026, 10, 29)


def make_document(document_id, title, place, topics, content, *, active=True, uploader=None):
    return {
        "id": document_id,
        "title": title,
        "place": place,
        "topics": topics,
        "content": content,
        "is_active": active,
        "archive_path": None if uploader else f"{title}.pdf",
        "uploader_name": uploader,
        "uploaded_at": datetime(2026, 10, 28, 12, 0),
    }


class FakeDatabase:
    """Documents in memory, one part per document; same filters as the SQL in bot/db.py."""

    def __init__(self, documents):
        self.documents = documents

    async def find_documents(self, pool, places, topics):
        found = [
            document for document in self.documents
            if document["is_active"] and document["place"] in places
            and (not topics or set(document["topics"]) & set(topics))
        ]
        return sorted(found, key=lambda document: (places.index(document["place"]), document["id"]))

    async def fetch_chunks(self, pool, document_ids):
        return [
            {"document_id": d["id"], "position": 0, "content": d["content"], "topics": d["topics"]}
            for d in self.documents if d["id"] in document_ids
        ]

    async def places_with_documents(self, pool, topics):
        return list({
            d["place"] for d in self.documents
            if d["is_active"] and (not topics or set(d["topics"]) & set(topics))
        })


class FakeLLM:
    """Returns prepared results and remembers what the bot sent to the answer model."""

    def __init__(self, parsed, answers=()):
        self.parsed = parsed
        self.answers = list(answers)
        self.calls = []  # (place chain, titles of the documents) of every answer request

    async def parse_question(self, question):
        if isinstance(self.parsed, Exception):
            raise self.parsed
        return self.parsed

    async def answer_question(self, question, chain, documents, today):
        self.calls.append((list(chain), [document["title"] for document in documents]))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def found(text, *used, parts_differ=False):
    return Answer(text=text, used_documents=list(used), parts_differ=parts_differ)


NOTHING = found("Такої поради немає.")

# The archive traps from the customer's documents, shortened
FORD_RIVER = make_document(1, "Броди біля річки", "біля річки", ["броди"], "Брід вище греблі можна, коли вода спала.")
FORD_GREBLIA = make_document(2, "Небезпеки Біля бобрової греблі", "Біля бобрової греблі", ["броди"], "Вбрід ніколи.")
FORD_DUB = make_document(3, "Стежки Під дубом", "Під дубом", ["броди"], "До річки через брід.")
FORD_OLD = make_document(4, "Броди старі", "Біля бобрової греблі", ["броди"], "Брід завжди.", active=False)
WARM_LEAF = make_document(5, "Як утеплити листяний", "листяний", ["утеплення"], "Дубове листя у 2 лапи.")
WARM_RIVER = make_document(6, "Як утеплити біля річки", "біля річки", ["утеплення"], "Очерет, мох гниє.")
MUSHROOMS_FOREST = make_document(7, "Осінній путівник", "весь ліс", ["гриби"], "Гриби після дощу.")
MUSHROOMS_PINE = make_document(8, "Грибний путівник хвойного", "хвойний", ["гриби"], "Гриби на 2-3 день.")
FEE_DUB = make_document(9, "Членські внески", "Під дубом", ["внески"], "Внесок 4 жолуді.", uploader="Їжак")


@pytest.fixture
def database(monkeypatch):
    database = FakeDatabase([])
    monkeypatch.setattr(search, "find_documents", database.find_documents)
    monkeypatch.setattr(search, "fetch_chunks", database.fetch_chunks)
    monkeypatch.setattr(search, "places_with_documents", database.places_with_documents)
    return database


async def ask(database, documents, llm, question="питання"):
    database.documents = documents
    return await answer_question(question, pool=None, llm=llm, today=QUESTION_DATE)


# --- a place is named ---

async def test_reply_has_answer_and_source_lines_built_by_the_bot(database):
    llm = FakeLLM(ParsedQuestion("Під дубом", ["внески"], []), [found("Внесок — 4 жолуді.", 0)])

    reply = await ask(database, [FEE_DUB], llm, "скільки внесок під дубом?")

    assert reply == "\n".join([
        "Внесок — 4 жолуді.",
        "",
        texts.SOURCES_HEADER,
        "📄 «Членські внески» — місце: Під дубом, надіслав(-ла) Їжак, 28.10.2026",
    ])


async def test_ford_question_sends_mitten_document_first_and_nothing_from_other_places(database):
    llm = FakeLLM(ParsedQuestion("Біля бобрової греблі", ["броди"], []), [found("Тільки по греблі.", 0)])

    reply = await ask(database, [FORD_RIVER, FORD_GREBLIA, FORD_DUB, FORD_OLD], llm, "як перейти річку біля греблі?")

    chain, titles = llm.calls[0]
    assert chain == ["Біля бобрової греблі", "біля річки", "весь ліс"]
    # Mitten before its forest part; another mitten and the replaced version never reach the LLM
    assert titles == ["Небезпеки Біля бобрової греблі", "Броди біля річки"]
    assert reply.endswith("📄 «Небезпеки Біля бобрової греблі» — місце: Біля бобрової греблі, Лісовий архів")


async def test_different_advice_of_two_forest_parts_gets_a_warning_and_both_sources(database):
    llm = FakeLLM(ParsedQuestion("Під вербою", ["утеплення"], []), [found("Листя або очерет.", 0, 1, parts_differ=True)])

    reply = await ask(database, [WARM_LEAF, WARM_RIVER], llm, "як утеплити рукавичку під вербою?")

    assert texts.PARTS_DIFFER.format(place="Під вербою") in reply
    assert "«Як утеплити листяний» — місце: листяний" in reply
    assert "«Як утеплити біля річки» — місце: біля річки" in reply


async def test_no_parts_warning_for_a_mitten_in_one_forest_part(database):
    llm = FakeLLM(ParsedQuestion("Біля бобрової греблі", ["броди"], []), [found("...", 0, parts_differ=True)])

    reply = await ask(database, [FORD_GREBLIA], llm)

    assert texts.PARTS_DIFFER.format(place="Біля бобрової греблі") not in reply


async def test_no_documents_for_the_place_means_not_found_without_asking_the_llm(database):
    llm = FakeLLM(ParsedQuestion("Під дубом", ["гриби"], []))

    reply = await ask(database, [MUSHROOMS_PINE], llm)

    assert reply == texts.NOT_FOUND
    assert llm.calls == []


# --- no place named ---

async def test_question_without_place_gets_whole_forest_advice_and_a_note(database):
    llm = FakeLLM(ParsedQuestion(None, ["гриби"], []), [found("Після дощу.", 0)])

    reply = await ask(database, [MUSHROOMS_FOREST, MUSHROOMS_PINE], llm, "коли шукати гриби?")

    assert llm.calls[0] == (["весь ліс"], ["Осінній путівник"])
    assert reply.endswith(texts.LOCAL_RULES_NOTE)


async def test_without_whole_forest_advice_the_bot_lists_places_that_have_it(database):
    llm = FakeLLM(ParsedQuestion(None, ["броди"], []))

    reply = await ask(database, [FORD_GREBLIA, FORD_RIVER, FORD_DUB, FORD_OLD], llm, "як перейти річку?")

    # Map order: forest parts before mittens; the whole forest was just searched and is not offered
    assert reply == texts.ASK_PLACE.format(places="біля річки, Під дубом, Біля бобрової греблі")


async def test_question_without_place_and_topic_is_not_found(database):
    llm = FakeLLM(ParsedQuestion(None, [], []))

    reply = await ask(database, [FORD_RIVER], llm, "котра година?")

    assert reply == texts.NOT_FOUND


# --- unclear place ---

async def test_ambiguous_place_asks_which_of_the_options(database):
    llm = FakeLLM(ParsedQuestion(UNKNOWN_PLACE, ["гриби"], ["Під ялиною", "Біля мурашника"]))

    reply = await ask(database, [MUSHROOMS_PINE], llm, "які гриби біля ялини?")

    assert reply == texts.ASK_WHICH_PLACE.format(options="«Під ялиною» чи «Біля мурашника»")


async def test_place_outside_the_map_asks_for_a_place_with_advice(database):
    llm = FakeLLM(ParsedQuestion(UNKNOWN_PLACE, ["гриби"], []))

    reply = await ask(database, [MUSHROOMS_PINE, MUSHROOMS_FOREST], llm, "які гриби під березою?")

    assert reply == texts.ASK_PLACE.format(places="весь ліс, хвойний")


# --- second try ---

async def test_second_try_looks_at_other_documents_by_question_words(database):
    # Advice on fees tagged only as "чергування": the topic filter misses it, the question words find it
    mistagged = make_document(10, "Чергування Під дубом", "Під дубом", ["чергування"], "Внесок віддавати Сові.")
    llm = FakeLLM(ParsedQuestion("Під дубом", ["внески"], []), [NOTHING, found("Сові.", 0)])

    reply = await ask(database, [FEE_DUB, mistagged], llm, "кому віддавати внесок під дубом?")

    assert llm.calls[0][1] == ["Членські внески"]
    # The part already sent is not sent again
    assert llm.calls[1][1] == ["Чергування Під дубом"]
    assert reply.endswith("📄 «Чергування Під дубом» — місце: Під дубом, Лісовий архів")


async def test_nothing_found_twice_means_not_found_and_no_sources(database):
    mistagged = make_document(10, "Чергування Під дубом", "Під дубом", ["чергування"], "Внесок віддавати Сові.")
    llm = FakeLLM(ParsedQuestion("Під дубом", ["внески"], []), [NOTHING, NOTHING])

    reply = await ask(database, [FEE_DUB, mistagged], llm, "кому віддавати внесок під дубом?")

    assert reply == texts.NOT_FOUND


# --- Groq is down ---

async def test_failed_question_parsing_asks_to_try_later(database):
    llm = FakeLLM(LLMUnavailableError())

    assert await ask(database, [FEE_DUB], llm) == texts.TRY_LATER


async def test_failed_answer_asks_to_try_later(database):
    llm = FakeLLM(ParsedQuestion("Під дубом", ["внески"], []), [LLMUnavailableError()])

    assert await ask(database, [FEE_DUB], llm) == texts.TRY_LATER


# --- format_source ---

def test_archive_source_names_the_archive_instead_of_an_author():
    assert format_source(MUSHROOMS_PINE) == "📄 «Грибний путівник хвойного» — місце: хвойний, Лісовий архів"


def test_uploaded_source_names_the_author_and_the_date():
    assert format_source(FEE_DUB) == "📄 «Членські внески» — місце: Під дубом, надіслав(-ла) Їжак, 28.10.2026"

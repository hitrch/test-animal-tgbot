"""LLM wrapper with the Groq client replaced by a mock: no network, no API key."""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import groq
import httpx
import pytest

from bot.llm import LLM, MIXED_PLACES, UNKNOWN_PLACE, UPLOAD_MAX_RETRIES, LLMUnavailableError

QUESTION_DATE = date(2026, 10, 29)


def groq_reply(data: dict) -> SimpleNamespace:
    """Same shape as a Groq chat completion: response.choices[0].message.content."""
    message = SimpleNamespace(content=json.dumps(data, ensure_ascii=False))
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def groq_error(error_class, status: int, body: dict | None = None):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.groq.com"))
    return error_class("Groq error", response=response, body=body)


def broken_json_error():
    return groq_error(groq.BadRequestError, 400, {"error": {"code": "json_validate_failed"}})


@pytest.fixture
def client():
    mock = MagicMock()
    # with_options returns a copy of the client with other settings: here the same mock
    mock.with_options.return_value = mock
    mock.chat.completions.create = AsyncMock()
    return mock


@pytest.fixture
def llm(client, monkeypatch):
    # LLM creates groq.AsyncGroq(...) inside: make it get the mock instead of a real client
    monkeypatch.setattr(groq, "AsyncGroq", MagicMock(return_value=client))
    return LLM(api_key="test", parse_model="parse-model", answer_model="answer-model")


def sent_messages(client) -> list[dict]:
    return client.chat.completions.create.call_args.kwargs["messages"]


# --- parse_question ---

async def test_question_place_and_topics_are_returned(llm, client):
    client.chat.completions.create.return_value = groq_reply(
        {"place": "Під дубом", "topics": ["внески"], "options": []}
    )

    parsed = await llm.parse_question("скільки внесок під дубом?")

    assert (parsed.place, parsed.topics, parsed.options) == ("Під дубом", ["внески"], [])
    assert client.chat.completions.create.call_args.kwargs["model"] == "parse-model"


async def test_question_place_outside_the_map_becomes_unknown(llm, client):
    client.chat.completions.create.return_value = groq_reply({"place": "Під березою", "topics": []})

    parsed = await llm.parse_question("що під березою?")

    assert parsed.place == UNKNOWN_PLACE


async def test_question_without_place_keeps_none(llm, client):
    client.chat.completions.create.return_value = groq_reply({"place": None, "topics": ["гриби"]})

    parsed = await llm.parse_question("коли шукати гриби?")

    assert parsed.place is None


async def test_unknown_topics_and_options_are_dropped(llm, client):
    client.chat.completions.create.return_value = groq_reply({
        "place": UNKNOWN_PLACE,
        "topics": ["гриби", "танці"],
        "options": ["Під ялиною", "Біля мурашника", "Під березою"],
    })

    parsed = await llm.parse_question("які гриби біля ялини?")

    assert parsed.topics == ["гриби"]
    assert parsed.options == ["Під ялиною", "Біля мурашника"]


# --- parse_chunk ---

async def test_chunk_place_outside_the_map_becomes_none(llm, client):
    client.chat.completions.create.return_value = groq_reply({"place": "десь", "topics": ["гриби"]})

    parsed = await llm.parse_chunk("Путівник", 1, 1, "текст")

    assert parsed.place is None


async def test_chunk_with_several_places_stays_mixed(llm, client):
    client.chat.completions.create.return_value = groq_reply({"place": MIXED_PLACES, "topics": ["гриби"]})

    parsed = await llm.parse_chunk("Путівник", 1, 1, "текст")

    assert parsed.place == MIXED_PLACES


async def test_chunk_without_known_topics_gets_inshe(llm, client):
    client.chat.completions.create.return_value = groq_reply({"place": "хвойний", "topics": ["танці"]})

    parsed = await llm.parse_chunk("Путівник", 1, 1, "текст")

    assert parsed.topics == ["інше"]


async def test_chunk_parsing_waits_out_rate_limits_longer(llm, client):
    client.chat.completions.create.return_value = groq_reply({"place": "хвойний", "topics": ["гриби"]})

    await llm.parse_chunk("Путівник", 2, 5, "текст")

    client.with_options.assert_called_with(max_retries=UPLOAD_MAX_RETRIES)
    assert "частина 2 з 5" in sent_messages(client)[1]["content"]


# --- answer_question ---

DOCUMENTS = [
    {"title": "Броди і гребля", "place": "Біля бобрової греблі", "content": "Вбрід ніколи."},
    {"title": "Броди біля річки", "place": "біля річки", "content": "Брід вище греблі."},
]
GREBLIA_CHAIN = ["Біля бобрової греблі", "біля річки", "весь ліс"]
VERBA_CHAIN = ["Під вербою", "листяний", "біля річки", "весь ліс"]


async def test_answer_sources_are_turned_into_list_indexes(llm, client):
    client.chat.completions.create.return_value = groq_reply(
        {"answer": "Лише по греблі.", "sources": [1], "conflict": False}
    )

    answer = await llm.answer_question("як перейти річку?", GREBLIA_CHAIN, DOCUMENTS, QUESTION_DATE)

    assert answer.text == "Лише по греблі."
    assert answer.used_documents == [0]
    assert answer.parts_differ is False
    assert client.chat.completions.create.call_args.kwargs["model"] == "answer-model"


async def test_answer_sources_outside_the_list_are_ignored(llm, client):
    client.chat.completions.create.return_value = groq_reply(
        {"answer": "...", "sources": [0, 2, 3, "1"], "conflict": False}
    )

    answer = await llm.answer_question("питання", GREBLIA_CHAIN, DOCUMENTS, QUESTION_DATE)

    assert answer.used_documents == [1]


async def test_answer_text_gets_real_line_breaks(llm, client):
    client.chat.completions.create.return_value = groq_reply({"answer": "Рядок\\nще рядок", "sources": [1]})

    answer = await llm.answer_question("питання", GREBLIA_CHAIN, DOCUMENTS, QUESTION_DATE)

    assert answer.text == "Рядок\nще рядок"


async def test_answer_conflict_is_true_only_for_true(llm, client):
    client.chat.completions.create.return_value = groq_reply({"answer": "...", "sources": [1], "conflict": "так"})

    answer = await llm.answer_question("питання", GREBLIA_CHAIN, DOCUMENTS, QUESTION_DATE)

    assert answer.parts_differ is False


async def test_answer_prompt_has_documents_with_places_and_today(llm, client):
    client.chat.completions.create.return_value = groq_reply({"answer": "...", "sources": []})

    await llm.answer_question("як перейти річку?", GREBLIA_CHAIN, DOCUMENTS, QUESTION_DATE)

    system, user = (message["content"] for message in sent_messages(client))
    assert "29.10.2026" in system
    assert "Документ 1: «Броди і гребля», місце: Біля бобрової греблі" in user
    assert "Документ 2: «Броди біля річки», місце: біля річки" in user
    assert "Питання звіра: як перейти річку?" in user


@pytest.mark.parametrize(("chain", "warned"), [(VERBA_CHAIN, True), (GREBLIA_CHAIN, False)])
async def test_mitten_in_two_forest_parts_gets_an_extra_warning(llm, client, chain, warned):
    client.chat.completions.create.return_value = groq_reply({"answer": "...", "sources": []})

    await llm.answer_question("як утеплити рукавичку?", chain, DOCUMENTS, QUESTION_DATE)

    user = sent_messages(client)[1]["content"]
    assert ("належить одразу до двох частин лісу" in user) is warned


# --- errors ---

async def test_broken_json_is_asked_again(llm, client):
    client.chat.completions.create.side_effect = [
        broken_json_error(),
        groq_reply({"place": "Під дубом", "topics": ["внески"]}),
    ]

    parsed = await llm.parse_question("скільки внесок під дубом?")

    assert parsed.place == "Під дубом"
    assert client.chat.completions.create.call_count == 2


async def test_broken_json_twice_means_llm_is_unavailable(llm, client):
    client.chat.completions.create.side_effect = [broken_json_error(), broken_json_error()]

    with pytest.raises(LLMUnavailableError):
        await llm.parse_question("питання")
    assert client.chat.completions.create.call_count == 2


async def test_other_bad_request_is_not_asked_again(llm, client):
    client.chat.completions.create.side_effect = groq_error(
        groq.BadRequestError, 400, {"error": {"code": "model_not_found"}}
    )

    with pytest.raises(LLMUnavailableError):
        await llm.parse_question("питання")
    assert client.chat.completions.create.call_count == 1


async def test_rate_limit_means_llm_is_unavailable(llm, client):
    client.chat.completions.create.side_effect = groq_error(groq.RateLimitError, 429)

    with pytest.raises(LLMUnavailableError):
        await llm.parse_question("питання")


async def test_network_error_means_llm_is_unavailable(llm, client):
    client.chat.completions.create.side_effect = groq.APIConnectionError(
        request=httpx.Request("POST", "https://api.groq.com")
    )

    with pytest.raises(LLMUnavailableError):
        await llm.answer_question("питання", GREBLIA_CHAIN, DOCUMENTS, QUESTION_DATE)


async def test_reply_that_is_not_json_means_llm_is_unavailable(llm, client):
    message = SimpleNamespace(content="не JSON")
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=message)])

    with pytest.raises(LLMUnavailableError):
        await llm.parse_question("питання")

"""Groq calls: parse a question and write an answer from documents."""
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import groq

from bot.places import ALL_PLACES
from bot.topics import TOPICS

logger = logging.getLogger(__name__)

# Returned as the place when the question names a place we cannot map to the forest map
UNKNOWN_PLACE = "unknown"
# Returned as the place when a document part has advice for several places
MIXED_PLACES = "mixed"

# A long document is parsed part by part, and Groq allows 8K tokens per minute:
# on a 429 error the SDK waits as long as the "retry-after" header says and tries again
UPLOAD_MAX_RETRIES = 10


class LLMUnavailableError(Exception):
    """Groq failed: rate limit, network problem or a broken response."""


@dataclass(frozen=True)
class ParsedQuestion:
    place: str | None  # one of ALL_PLACES, UNKNOWN_PLACE, or None if no place is named
    topics: list[str]
    options: list[str]  # for an ambiguous place: the places it may mean


@dataclass(frozen=True)
class Answer:
    text: str
    used_documents: list[int]  # indexes in the documents list; empty means nothing was found
    parts_differ: bool  # the answer shows different advice of two forest parts (Під вербою)


@dataclass(frozen=True)
class ParsedChunk:
    place: str | None  # one of ALL_PLACES, MIXED_PLACES, or None if the part does not say
    topics: list[str]


# Shared by the question and the document prompts
PLACES_TEXT = """- "весь ліс" — весь ліс загалом;
- "хвойний", "листяний", "біля річки" — частини лісу ("у хвойному лісі", "біля річки");
- рукавички: "Під ялиною", "У соснах", "Біля мурашника", "Під дубом", "У ліщині", "Під вербою",
  "Біля бобрової греблі", "На узліссі"."""

TOPICS_TEXT = "\n".join(f"- {name}: {description}" for name, description in TOPICS.items())


PARSE_PROMPT = f"""Ти розбираєш питання звірів про ліс. Визнач місце, про яке питають, і теми питання.

Місця (рівно одне з цього списку):
{PLACES_TEXT}
Підказки: "біля сосон" → "У соснах"; "під дубом", "біля дуба" → "Під дубом"; "біля ліщини" → "У ліщині";
"біля верби" → "Під вербою"; "біля греблі" → "Біля бобрової греблі"; "біля поля" → "На узліссі".
"біля ялини" неоднозначне (є "Під ялиною" і "Біля мурашника" під сухою ялиною) → "{UNKNOWN_PLACE}".
Якщо питають про дорогу, місце — звідки йдуть.
Якщо місце названо, але воно неоднозначне або його немає в списку → "{UNKNOWN_PLACE}".
Якщо воно неоднозначне, у "options" перелічи місця зі списку, які звір міг мати на увазі; інакше "options" порожній.
Якщо місце взагалі не назване → null.

Теми (одна або кілька з цього списку, назва — до двокрапки):
{TOPICS_TEXT}

Відповідай лише JSON: {{"place": <місце, "{UNKNOWN_PLACE}" або null>, "topics": [<теми>], "options": [<місця>]}}"""


CHUNK_PROMPT = f"""Ти розбираєш частину документа з порадами для звірів лісу. Визнач, для якого місця ці поради, і теми.

Місце — для кого написані поради, а не які місця в тексті згадуються.
Наприклад, «Стежки від рукавички Під дубом» згадують ліщину й вербу, але місце — "Під дубом".
Зазвичай місце видно з назви документа, заголовка чи вступу: «Для мешканців листяного лісу», «рукавичка «Під дубом»».

Місця (рівно одне з цього списку):
{PLACES_TEXT}
Якщо в цій частині є окремі поради для різних місць (наприклад, розділи для двох рукавичок
або для рукавички й усього лісу) → "{MIXED_PLACES}".
Якщо з назви документа й цієї частини не видно, для кого поради → null. Не вгадуй.
Якщо це не перша частина і в ній не сказано, для кого поради, → null: місце названо на початку документа.

Теми (одна або кілька з цього списку, назва — до двокрапки):
{TOPICS_TEXT}

Відповідай лише JSON: {{"place": <місце, "{MIXED_PLACES}" або null>, "topics": [<теми>]}}"""


ANSWER_PROMPT = """Ти — лісовий порадник. Відповідаєш звірам українською, просто, доброзичливо й коротко.

Правила:
1. Відповідай ЛИШЕ за наданими документами. Нічого не вигадуй і не додавай від себе.
2. У кожного документа вказане місце. Звір питає про місце з ланцюжка: від найконкретнішого до найзагальнішого.
   Найконкретніша порада головніша: рукавичка → частина лісу → весь ліс.
   Загальнішу пораду згадуй лише тоді, коли вона не суперечить конкретнішій.
3. Якщо для рукавички немає своєї поради, а поради двох частин лісу суперечать одна одній,
   наведи обидві, скажи, для якої частини кожна, і попередь, що вони відрізняються.
4. Якщо доречно, коротко додай пов'язані важливі деталі з того самого документа (строк, кому віддавати).
5. Якщо в документах немає відповіді на питання, чесно скажи, що такої поради не знайшов, і поверни порожній список джерел.
6. Не називай джерела й номери документів у тексті відповіді: їх додасть бот. Не використовуй Markdown (зірочки, решітки).
7. Сьогодні {today}. Враховуй це, якщо порада залежить від пори року.

Відповідай лише JSON: {{"answer": "<текст відповіді>", "sources": [<номери документів, якими скористався>],
"conflict": <true, якщо навів різні поради двох частин лісу, інакше false>}}"""


class LLM:
    def __init__(self, api_key: str, parse_model: str, answer_model: str) -> None:
        self._client = groq.AsyncGroq(api_key=api_key)
        self._parse_model = parse_model
        self._answer_model = answer_model

    async def parse_question(self, question: str) -> ParsedQuestion:
        data = await self._ask_json(self._parse_model, PARSE_PROMPT, question)

        place = data.get("place")
        if place is not None and place not in ALL_PLACES:
            place = UNKNOWN_PLACE
        topics = [topic for topic in data.get("topics", []) if topic in TOPICS]
        options = [option for option in data.get("options", []) if option in ALL_PLACES]
        return ParsedQuestion(place=place, topics=topics, options=options)

    async def parse_chunk(self, title: str, number: int, total: int, text: str) -> ParsedChunk:
        """Place and topics of one part of an uploaded document. number counts from 1."""
        user = f"Документ «{title}», частина {number} з {total}:\n\n{text}"
        data = await self._ask_json(self._parse_model, CHUNK_PROMPT, user, max_retries=UPLOAD_MAX_RETRIES)

        place = data.get("place")
        # A place outside the list is as good as no place: the bot will ask the uploader
        if place not in ALL_PLACES and place != MIXED_PLACES:
            place = None
        # Every part needs a topic, otherwise the search would never find it
        topics = [topic for topic in data.get("topics", []) if topic in TOPICS] or ["інше"]
        return ParsedChunk(place=place, topics=topics)

    async def answer_question(
        self,
        question: str,
        place_chain: Sequence[str],
        documents: Sequence[Mapping],
        today: date,
    ) -> Answer:
        """documents: rows with "title", "place" and "content"."""
        parts = [f"Ланцюжок місць для питання: {' → '.join(place_chain)}"]
        # A mitten in two forest parts (Під вербою): the general rule alone was sometimes ignored,
        # so repeat it for this exact question
        forest_parts = list(place_chain[1:-1])
        if len(forest_parts) > 1:
            parts.append(
                f"Увага: рукавичка «{place_chain[0]}» належить одразу до двох частин лісу: "
                f"{' і '.join(forest_parts)}. Якщо для самої рукавички поради немає, а поради цих частин "
                "різні, обов'язково наведи обидві, скажи, для якої частини кожна, і вкажи обидва документи в джерелах."
            )
        for number, document in enumerate(documents, start=1):
            parts.append(
                f"--- Документ {number}: «{document['title']}», місце: {document['place']}\n{document['content']}"
            )
        parts.append(f"--- Питання звіра: {question}")

        system = ANSWER_PROMPT.format(today=today.strftime("%d.%m.%Y"))
        data = await self._ask_json(self._answer_model, system, "\n\n".join(parts))

        # The model numbers documents from 1, we keep list indexes from 0
        used = [n - 1 for n in data.get("sources", []) if isinstance(n, int) and 1 <= n <= len(documents)]
        # Sometimes the model escapes line breaks twice and a literal "\n" ends up in the text
        text = str(data.get("answer", "")).replace("\\n", "\n").strip()
        return Answer(
            text=text,
            used_documents=used,
            parts_differ=data.get("conflict") is True,
        )

    async def _ask_json(self, model: str, system: str, user: str, max_retries: int = 2) -> dict:
        try:
            # with_options makes a copy of the client with other settings; 2 retries is the SDK default
            client = self._client.with_options(max_retries=max_retries)
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                reasoning_effort="low",
            )
            return json.loads(response.choices[0].message.content)
        except (groq.APIError, json.JSONDecodeError) as error:
            logger.warning("Groq request to %s failed: %r", model, error)
            raise LLMUnavailableError from error
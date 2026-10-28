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


PARSE_PROMPT = f"""Ти розбираєш питання звірів про ліс. Визнач місце, про яке питають, і теми питання.

Місця (рівно одне з цього списку):
- "весь ліс" — якщо питають про весь ліс загалом;
- "хвойний", "листяний", "біля річки" — частини лісу ("у хвойному лісі", "біля річки");
- рукавички: "Під ялиною", "У соснах", "Біля мурашника", "Під дубом", "У ліщині", "Під вербою",
  "Біля бобрової греблі", "На узліссі".
Підказки: "біля сосон" → "У соснах"; "під дубом", "біля дуба" → "Під дубом"; "біля ліщини" → "У ліщині";
"біля верби" → "Під вербою"; "біля греблі" → "Біля бобрової греблі"; "біля поля" → "На узліссі".
"біля ялини" неоднозначне (є "Під ялиною" і "Біля мурашника" під сухою ялиною) → "{UNKNOWN_PLACE}".
Якщо питають про дорогу, місце — звідки йдуть.
Якщо місце названо, але воно неоднозначне або його немає в списку → "{UNKNOWN_PLACE}".
Якщо воно неоднозначне, у "options" перелічи місця зі списку, які звір міг мати на увазі; інакше "options" порожній.
Якщо місце взагалі не назване → null.

Теми (одна або кілька з цього списку, назва — до двокрапки):
{chr(10).join(f"- {name}: {description}" for name, description in TOPICS.items())}

Відповідай лише JSON: {{"place": <місце, "{UNKNOWN_PLACE}" або null>, "topics": [<теми>], "options": [<місця>]}}"""


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

    async def _ask_json(self, model: str, system: str, user: str) -> dict:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                reasoning_effort="low",
            )
            return json.loads(response.choices[0].message.content)
        except (groq.APIError, json.JSONDecodeError) as error:
            logger.warning("Groq request to %s failed: %r", model, error)
            raise LLMUnavailableError from error
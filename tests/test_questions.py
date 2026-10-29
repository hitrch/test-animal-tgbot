from datetime import date
from unittest.mock import AsyncMock

from bot.handlers import questions
from tests.fakes import NoChatAction, make_message, replies


async def test_question_text_is_answered_with_todays_date(monkeypatch):
    answer_question = AsyncMock(return_value="Внесок — 4 жолуді.")
    monkeypatch.setattr(questions, "answer_question", answer_question)
    monkeypatch.setattr(questions, "ChatActionSender", NoChatAction)
    message = make_message("скільки внесок під дубом?")

    await questions.question(message, pool=None, llm="llm")

    # The real date goes in here: seasonal advice depends on it
    answer_question.assert_awaited_once_with("скільки внесок під дубом?", None, "llm", date.today())
    assert replies(message) == ["Внесок — 4 жолуді."]
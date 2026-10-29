from bot.chunks import Chunk, all_topics, select_chunks, split_text

GREBLIA_CHAIN = ["Біля бобрової греблі", "біля річки", "весь ліс"]


def make_document(document_id, place="біля річки", title="Документ"):
    return {"id": document_id, "place": place, "title": title}


def make_chunk(document_id, position, content, topics=("броди",)):
    return {"document_id": document_id, "position": position, "content": content, "topics": list(topics)}


# --- split_text ---

def test_short_text_stays_one_part():
    assert split_text("перший рядок\nдругий рядок", size=100) == ["перший рядок\nдругий рядок"]


def test_text_is_split_at_line_borders():
    assert split_text("aaaa\nbbbb\ncccc", size=9) == ["aaaa\nbbbb", "cccc"]


def test_empty_lines_and_spaces_around_lines_are_dropped():
    assert split_text("  перший  \n\n\n  другий", size=100) == ["перший\nдругий"]


def test_long_line_is_cut_at_a_space():
    assert split_text("один два три", size=8) == ["один", "два три"]


def test_line_without_spaces_is_cut_at_the_limit():
    assert split_text("a" * 10, size=4) == ["aaaa", "aaaa", "aa"]


def test_parts_fit_the_size_and_no_words_are_lost():
    text = "\n".join(f"Порада номер {n}: шукай гриби після дощу біля старого пня" for n in range(200))

    parts = split_text(text, size=500)

    assert len(parts) > 1
    assert all(len(part) <= 500 for part in parts)
    assert " ".join(parts).split() == text.split()


# --- all_topics ---

def test_document_topics_are_all_part_topics_without_repeats_in_list_order():
    chunks = [Chunk("...", ["пожива", "гриби"]), Chunk("...", ["внески", "гриби"])]

    assert all_topics(chunks) == ["внески", "гриби", "пожива"]


# --- select_chunks ---

def test_more_specific_place_wins_when_only_one_part_fits():
    documents = [make_document(1, "біля річки"), make_document(2, "Біля бобрової греблі")]
    chunks = [make_chunk(1, 0, "р" * 50), make_chunk(2, 0, "г" * 50)]

    result = select_chunks(documents, chunks, GREBLIA_CHAIN, "як перейти річку?", ["броди"], budget=60)

    assert [document["id"] for document in result] == [2]


def test_within_a_place_part_sharing_more_question_words_goes_first():
    documents = [make_document(1)]
    chunks = [
        make_chunk(1, 0, "Жолуді збирають восени під дубами"),
        make_chunk(1, 1, "Гриби збирати наступного дня після дощу"),
    ]

    result = select_chunks(documents, chunks, GREBLIA_CHAIN, "коли збирати гриби?", [], budget=45)

    assert result[0]["positions"] == [1]


def test_part_that_does_not_fit_is_skipped_but_a_smaller_one_is_taken():
    documents = [make_document(1)]
    chunks = [make_chunk(1, 0, "а" * 50), make_chunk(1, 1, "б" * 50), make_chunk(1, 2, "в" * 10)]

    result = select_chunks(documents, chunks, GREBLIA_CHAIN, "питання", [], budget=65)

    assert result[0]["positions"] == [0, 2]


def test_best_part_is_taken_even_if_bigger_than_the_budget():
    documents = [make_document(1)]
    chunks = [make_chunk(1, 0, "а" * 100)]

    result = select_chunks(documents, chunks, GREBLIA_CHAIN, "питання", [], budget=50)

    assert result[0]["positions"] == [0]


def test_only_parts_on_the_question_topics_are_chosen():
    documents = [make_document(1)]
    chunks = [make_chunk(1, 0, "про гриби", ["гриби"]), make_chunk(1, 1, "про внески", ["внески"])]

    result = select_chunks(documents, chunks, GREBLIA_CHAIN, "скільки внесок?", ["внески"])

    assert result[0]["content"] == "про внески"


def test_without_topics_parts_on_any_topic_are_chosen():
    documents = [make_document(1)]
    chunks = [make_chunk(1, 0, "про гриби", ["гриби"]), make_chunk(1, 1, "про внески", ["внески"])]

    result = select_chunks(documents, chunks, GREBLIA_CHAIN, "питання", [])

    assert result[0]["positions"] == [0, 1]


def test_question_words_like_koly_do_not_count_as_shared_words():
    documents = [make_document(1)]
    chunks = [
        make_chunk(1, 0, "Коли треба прибирати стежку"),
        make_chunk(1, 1, "Внесок платити до першого снігу"),
    ]

    result = select_chunks(
        documents, chunks, GREBLIA_CHAIN, "коли треба платити внесок?", [], require_shared_words=True
    )

    assert result[0]["positions"] == [1]


def test_word_forms_match_by_the_first_letters():
    documents = [make_document(1)]
    chunks = [make_chunk(1, 0, "Грибів тут багато")]

    result = select_chunks(documents, chunks, GREBLIA_CHAIN, "де гриби?", [], require_shared_words=True)

    assert len(result) == 1


def test_parts_are_glued_back_in_document_order_and_other_fields_are_kept():
    documents = [make_document(1, title="Броди")]
    chunks = [make_chunk(1, 2, "третя"), make_chunk(1, 0, "перша"), make_chunk(1, 1, "друга", ["гриби"])]

    result = select_chunks(documents, chunks, GREBLIA_CHAIN, "питання", ["броди"])

    assert result == [{
        "id": 1, "place": "біля річки", "title": "Броди",
        "content": "перша\n\nтретя", "positions": [0, 2],
    }]


def test_documents_without_chosen_parts_and_unknown_parts_are_left_out():
    documents = [make_document(1), make_document(2)]
    chunks = [make_chunk(1, 0, "про броди"), make_chunk(2, 0, "про гриби", ["гриби"]), make_chunk(99, 0, "чужа")]

    result = select_chunks(documents, chunks, GREBLIA_CHAIN, "питання", ["броди"])

    assert [document["id"] for document in result] == [1]

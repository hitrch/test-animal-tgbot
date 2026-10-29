"""SQL of bot/db.py on a real Postgres (the db-test service); skipped if it is not running."""
import asyncpg
import pytest

from bot.chunks import Chunk
from bot.db import (
    add_consent,
    add_document,
    delete_document,
    document_exists,
    fetch_chunks,
    find_documents,
    find_similar_documents,
    get_own_document,
    has_consent,
    init_schema,
    places_with_documents,
    user_documents,
)

HEDGEHOG = 1  # Telegram user ids
FOX = 2


async def add(pool, title, place="Під дубом", topics=("внески",), *, uploader=HEDGEHOG, replaces=None):
    """A document with one part; uploader=None makes it an archive document."""
    return await add_document(
        pool,
        title=title,
        place=place,
        chunks=[Chunk(content=f"Текст «{title}»", topics=list(topics))],
        file_hash=title,
        telegram_file_id=f"file-{title}" if uploader else None,
        archive_path=None if uploader else f"{title}.pdf",
        uploader_id=uploader,
        uploader_name="Їжак" if uploader else None,
        replaces=replaces,
    )


async def titles(rows):
    return [row["title"] for row in rows]


async def is_active(pool, document_id):
    return await pool.fetchval("SELECT is_active FROM documents WHERE id = $1", document_id)


# --- schema ---

async def test_schema_can_be_applied_again_on_every_start(pool):
    await add(pool, "Внески")

    await init_schema(pool)

    assert await document_exists(pool, "Внески")


async def test_database_rejects_a_place_outside_the_map(pool):
    with pytest.raises(asyncpg.CheckViolationError):
        await add(pool, "Внески", place="Під березою")


async def test_database_rejects_the_same_file_twice(pool):
    await add(pool, "Внески")

    with pytest.raises(asyncpg.UniqueViolationError):
        await add(pool, "Внески")


# --- consent ---

async def test_consent_is_saved_and_pressing_twice_is_fine(pool):
    assert not await has_consent(pool, HEDGEHOG)

    await add_consent(pool, HEDGEHOG)
    await add_consent(pool, HEDGEHOG)

    assert await has_consent(pool, HEDGEHOG)
    assert not await has_consent(pool, FOX)


# --- saving and searching ---

async def test_document_is_saved_with_its_parts_and_all_their_topics(pool):
    document_id = await add_document(
        pool,
        title="Путівник",
        place="хвойний",
        chunks=[Chunk("перша", ["пожива", "гриби"]), Chunk("друга", ["гриби", "небезпеки"])],
        file_hash="hash",
        telegram_file_id="file",
    )

    topics = await pool.fetchval("SELECT topics FROM documents WHERE id = $1", document_id)
    chunks = await fetch_chunks(pool, [document_id])
    assert topics == ["гриби", "небезпеки", "пожива"]
    assert [(chunk["position"], chunk["content"]) for chunk in chunks] == [(0, "перша"), (1, "друга")]


async def test_documents_are_found_by_place_chain_and_topic_most_specific_first(pool):
    await add(pool, "Внески ліс", place="весь ліс")
    await add(pool, "Внески листяний", place="листяний")
    await add(pool, "Внески Під дубом", place="Під дубом")
    await add(pool, "Внески У ліщині", place="У ліщині")
    await add(pool, "Гриби Під дубом", place="Під дубом", topics=["гриби"])

    found = await find_documents(pool, ["Під дубом", "листяний", "весь ліс"], ["внески"])

    assert await titles(found) == ["Внески Під дубом", "Внески листяний", "Внески ліс"]


async def test_without_topics_all_documents_of_the_places_are_found(pool):
    await add(pool, "Внески", place="Під дубом")
    await add(pool, "Гриби", place="Під дубом", topics=["гриби"])
    await add(pool, "Внески У ліщині", place="У ліщині")

    found = await find_documents(pool, ["Під дубом"], [])

    assert await titles(found) == ["Внески", "Гриби"]


async def test_places_with_documents_on_the_topic(pool):
    await add(pool, "Внески Під дубом", place="Під дубом")
    await add(pool, "Гриби хвойний", place="хвойний", topics=["гриби"])

    assert await places_with_documents(pool, ["гриби"]) == ["хвойний"]
    assert sorted(await places_with_documents(pool, [])) == ["Під дубом", "хвойний"]


# --- new versions ---

async def test_new_version_turns_the_old_one_off(pool):
    old = await add(pool, "Внески 2025", uploader=None)
    new = await add(pool, "Внески 2026", uploader=FOX, replaces=old)

    found = await find_documents(pool, ["Під дубом"], ["внески"])

    assert await titles(found) == ["Внески 2026"]
    assert await pool.fetchval("SELECT replaced_by FROM documents WHERE id = $1", old) == new
    assert await places_with_documents(pool, ["внески"]) == ["Під дубом"]


async def test_similar_documents_share_a_topic_and_place_most_shared_first(pool):
    await add(pool, "Статут", topics=["керівництво", "внески"])
    await add(pool, "Внески", topics=["внески"])
    await add(pool, "Інше Під дубом", topics=["інше"])
    await add(pool, "Внески У ліщині", place="У ліщині", topics=["внески", "керівництво"])
    replaced = await add(pool, "Статут старий", topics=["керівництво", "внески"])
    await add(pool, "Статут новий", topics=["правила"], replaces=replaced)

    similar = await find_similar_documents(pool, "Під дубом", ["внески", "керівництво", "інше"])

    # "інше" alone does not make documents similar; replaced and other places are not offered
    assert await titles(similar) == ["Статут", "Внески"]


async def test_at_most_three_similar_documents(pool):
    for number in range(5):
        await add(pool, f"Внески {number}")

    assert len(await find_similar_documents(pool, "Під дубом", ["внески"])) == 3


# --- /my and deleting ---

async def test_user_sees_only_own_documents_with_the_newer_version(pool):
    mine = await add(pool, "Мої внески", uploader=HEDGEHOG)
    await add(pool, "Внески від Лиса", uploader=FOX, replaces=mine)
    await add(pool, "Архівні внески", uploader=None)

    rows = await user_documents(pool, HEDGEHOG)

    assert [(row["title"], row["is_active"], row["replaced_by_title"]) for row in rows] == [
        ("Мої внески", False, "Внески від Лиса"),
    ]


async def test_other_users_and_archive_documents_are_not_own(pool):
    mine = await add(pool, "Мої", uploader=HEDGEHOG)
    archive = await add(pool, "Архівні", uploader=None)

    assert (await get_own_document(pool, mine, HEDGEHOG))["title"] == "Мої"
    assert await get_own_document(pool, mine, FOX) is None
    assert await get_own_document(pool, archive, HEDGEHOG) is None


async def test_user_cannot_delete_other_users_or_archive_documents(pool):
    mine = await add(pool, "Мої", uploader=HEDGEHOG)
    archive = await add(pool, "Архівні", uploader=None)

    assert await delete_document(pool, mine, FOX) == (False, None)
    assert await delete_document(pool, archive, HEDGEHOG) == (False, None)
    assert await document_exists(pool, "Мої")
    assert await document_exists(pool, "Архівні")


async def test_deleting_a_document_deletes_its_parts(pool):
    mine = await add(pool, "Мої")

    assert await delete_document(pool, mine, HEDGEHOG) == (True, None)
    assert await fetch_chunks(pool, [mine]) == []


async def test_deleting_a_new_version_makes_the_old_one_work_again(pool):
    old = await add(pool, "Внески 2025", uploader=None)
    new = await add(pool, "Внески 2026", uploader=FOX, replaces=old)

    assert await delete_document(pool, new, FOX) == (True, "Внески 2025")

    assert await is_active(pool, old)
    assert await pool.fetchval("SELECT replaced_by FROM documents WHERE id = $1", old) is None


async def test_deleting_a_replaced_document_keeps_the_chain_and_the_newest_version(pool):
    first = await add(pool, "Внески 2024", uploader=None)
    middle = await add(pool, "Внески 2025", uploader=HEDGEHOG, replaces=first)
    newest = await add(pool, "Внески 2026", uploader=FOX, replaces=middle)

    # Nothing works again: the newest version is still there
    assert await delete_document(pool, middle, HEDGEHOG) == (True, None)

    assert not await is_active(pool, first)
    assert await pool.fetchval("SELECT replaced_by FROM documents WHERE id = $1", first) == newest
    assert await titles(await find_documents(pool, ["Під дубом"], [])) == ["Внески 2026"]


async def test_deleted_file_can_be_uploaded_again(pool):
    mine = await add(pool, "Мої")

    await delete_document(pool, mine, HEDGEHOG)

    assert not await document_exists(pool, "Мої")
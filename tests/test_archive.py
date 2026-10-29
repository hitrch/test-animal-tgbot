from collections import Counter

import pytest

from bot.archive import ARCHIVE_DIR, MANIFEST_NAME, OCR_DIR_NAME, load_archive, read_manifest
from bot.chunks import Chunk
from bot.db import add_document, fetch_chunks, find_documents
from bot.places import FOREST_PARTS, MITTENS, WHOLE_FOREST


@pytest.fixture(scope="module")
def manifest():
    return read_manifest(ARCHIVE_DIR / MANIFEST_NAME)


def test_every_manifest_file_is_in_the_archive(manifest):
    missing = [name for name in manifest if not (ARCHIVE_DIR / name).is_file()]
    assert missing == []


def test_archive_has_documents_on_every_level(manifest):
    places = Counter(place for place, _ in manifest.values())

    assert places[WHOLE_FOREST] == 7
    assert sum(places[part] for part in FOREST_PARTS) == 15
    # Every mitten has the same 7 kinds of documents
    assert all(places[mitten] == 7 for mitten in MITTENS)


def write_manifest(tmp_path, row):
    path = tmp_path / MANIFEST_NAME
    path.write_text(f"file;place;topics\n{row}\n", encoding="utf-8")
    return path


def test_manifest_row_is_read_as_place_and_topics(tmp_path):
    path = write_manifest(tmp_path, "Внески.pdf;Під дубом;внески,керівництво")

    assert read_manifest(path) == {"Внески.pdf": ("Під дубом", ["внески", "керівництво"])}


def test_unknown_place_in_manifest_is_an_error(tmp_path):
    path = write_manifest(tmp_path, "Внески.pdf;Під березою;внески")

    with pytest.raises(ValueError, match="unknown place"):
        read_manifest(path)


def test_unknown_topic_in_manifest_is_an_error(tmp_path):
    path = write_manifest(tmp_path, "Внески.pdf;Під дубом;внески,танці")

    with pytest.raises(ValueError, match="unknown topics"):
        read_manifest(path)


# --- loading into the database (test database, see conftest.py) ---

@pytest.fixture
def small_archive(tmp_path):
    """Two documents: a text file and a "scan" with proofread text in ocr/."""
    (tmp_path / "Внески Під дубом.txt").write_text("Внесок — 4 жолуді.", encoding="utf-8")
    # Not a real PDF: if the bot tried to read it instead of the proofread text, it would fail
    (tmp_path / "Гриби хвойного.pdf").write_bytes(b"scan")
    (tmp_path / OCR_DIR_NAME).mkdir()
    (tmp_path / OCR_DIR_NAME / "Гриби хвойного.txt").write_text("Гриби на 2-3 день.\n", encoding="utf-8")
    (tmp_path / MANIFEST_NAME).write_text(
        "file;place;topics\n"
        "Внески Під дубом.txt;Під дубом;внески\n"
        "Гриби хвойного.pdf;хвойний;гриби,пожива\n",
        encoding="utf-8",
    )
    return tmp_path


async def all_documents(pool):
    return await pool.fetch("SELECT id, title, place, topics, archive_path, is_active FROM documents ORDER BY id")


async def test_archive_documents_get_place_and_topics_from_the_manifest(pool, small_archive):
    await load_archive(pool, small_archive)

    rows = await all_documents(pool)
    assert [(row["title"], row["place"], row["topics"], row["archive_path"]) for row in rows] == [
        ("Внески Під дубом", "Під дубом", ["внески"], "Внески Під дубом.txt"),
        ("Гриби хвойного", "хвойний", ["гриби", "пожива"], "Гриби хвойного.pdf"),
    ]


async def test_proofread_text_is_used_instead_of_the_scan(pool, small_archive):
    await load_archive(pool, small_archive)

    found = await find_documents(pool, ["хвойний"], ["гриби"])
    chunks = await fetch_chunks(pool, [found[0]["id"]])
    assert [chunk["content"] for chunk in chunks] == ["Гриби на 2-3 день."]


async def test_restart_does_not_load_the_archive_again_over_a_new_version(pool, small_archive):
    await load_archive(pool, small_archive)
    fees = (await find_documents(pool, ["Під дубом"], ["внески"]))[0]
    await add_document(
        pool, title="Внески 2026", place="Під дубом", chunks=[Chunk("Внесок — 5 жолудів.", ["внески"])],
        file_hash="new", telegram_file_id="file", uploader_id=1, uploader_name="Їжак", replaces=fees["id"],
    )

    # The bot starts again
    await load_archive(pool, small_archive)

    assert len(await all_documents(pool)) == 3
    assert [row["title"] for row in await find_documents(pool, ["Під дубом"], ["внески"])] == ["Внески 2026"]


async def test_archive_file_missing_from_the_manifest_is_an_error(pool, small_archive):
    (small_archive / "Новий документ.txt").write_text("...", encoding="utf-8")

    with pytest.raises(ValueError, match="Новий документ.txt"):
        await load_archive(pool, small_archive)
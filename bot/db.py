from pathlib import Path

import asyncpg

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def init_schema(pool: asyncpg.Pool) -> None:
    """Create tables if they do not exist yet."""
    await pool.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


async def has_consent(pool: asyncpg.Pool, user_id: int) -> bool:
    return await pool.fetchval("SELECT EXISTS(SELECT 1 FROM consents WHERE user_id = $1)", user_id)


async def add_consent(pool: asyncpg.Pool, user_id: int) -> None:
    # Pressing the button twice is fine: the second insert is simply ignored
    await pool.execute("INSERT INTO consents (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)


async def find_documents(pool: asyncpg.Pool, places: list[str], topics: list[str]) -> list[asyncpg.Record]:
    """Active documents for the given places that share at least one topic with the question.

    With no topics, all active documents of these places are returned.
    Results are ordered like `places`: the most specific place first.
    """
    return await pool.fetch(
        """
        SELECT id, title, place, topics, content, archive_path, uploader_name, uploaded_at
        FROM documents
        WHERE is_active
          AND place = ANY($1)
          AND (cardinality($2::text[]) = 0 OR topics && $2)
        ORDER BY array_position($1, place), id
        """,
        places, topics,
    )


async def places_with_documents(pool: asyncpg.Pool, topics: list[str]) -> list[str]:
    """Places that have active documents on these topics (any topic if the list is empty)."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT place FROM documents
        WHERE is_active AND (cardinality($1::text[]) = 0 OR topics && $1)
        """,
        topics,
    )
    return [row["place"] for row in rows]


async def document_exists(pool: asyncpg.Pool, file_hash: str) -> bool:
    return await pool.fetchval("SELECT EXISTS(SELECT 1 FROM documents WHERE file_hash = $1)", file_hash)


async def add_document(
    pool: asyncpg.Pool,
    *,
    title: str,
    place: str,
    topics: list[str],
    content: str,
    file_hash: str,
    telegram_file_id: str | None = None,
    archive_path: str | None = None,
    uploader_id: int | None = None,
    uploader_name: str | None = None,
) -> int:
    """Insert a document and return its id."""
    return await pool.fetchval(
        """
        INSERT INTO documents
            (title, place, topics, content, file_hash, telegram_file_id, archive_path, uploader_id, uploader_name)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        title, place, topics, content, file_hash, telegram_file_id, archive_path, uploader_id, uploader_name,
    )
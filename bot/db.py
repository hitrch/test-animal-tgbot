from collections.abc import Sequence
from pathlib import Path

import asyncpg

from bot.chunks import Chunk, all_topics

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
        SELECT id, title, place, topics, archive_path, uploader_name, uploaded_at
        FROM documents
        WHERE is_active
          AND place = ANY($1)
          AND (cardinality($2::text[]) = 0 OR topics && $2)
        ORDER BY array_position($1, place), id
        """,
        places, topics,
    )


async def fetch_chunks(pool: asyncpg.Pool, document_ids: list[int]) -> list[asyncpg.Record]:
    return await pool.fetch(
        """
        SELECT document_id, position, content, topics FROM chunks
        WHERE document_id = ANY($1)
        ORDER BY document_id, position
        """,
        document_ids,
    )


async def find_similar_documents(pool: asyncpg.Pool, place: str, topics: list[str]) -> list[asyncpg.Record]:
    """Active documents of the same place sharing topics with a new one: candidates for "new version".

    The most shared topics first. "інше" is ignored: almost everything would look similar by it.
    """
    topics = [topic for topic in topics if topic != "інше"]
    return await pool.fetch(
        """
        SELECT id, title, topics FROM documents
        WHERE is_active AND place = $1 AND topics && $2
        ORDER BY cardinality(ARRAY(SELECT unnest(topics) INTERSECT SELECT unnest($2::text[]))) DESC, id
        LIMIT 3
        """,
        place, topics,
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


async def user_documents(pool: asyncpg.Pool, user_id: int) -> list[asyncpg.Record]:
    """Documents uploaded by the user, newest first, with the title of the newer version if replaced."""
    # The table is joined with itself: "newer" is the document that replaced this one (if any)
    return await pool.fetch(
        """
        SELECT d.id, d.title, d.place, d.uploaded_at, d.is_active, newer.title AS replaced_by_title
        FROM documents d
        LEFT JOIN documents newer ON newer.id = d.replaced_by
        WHERE d.uploader_id = $1
        ORDER BY d.uploaded_at DESC
        """,
        user_id,
    )


async def get_own_document(pool: asyncpg.Pool, document_id: int, user_id: int) -> asyncpg.Record | None:
    """The document if this user uploaded it; None for other users' and archive documents."""
    return await pool.fetchrow(
        "SELECT id, title FROM documents WHERE id = $1 AND uploader_id = $2",
        document_id, user_id,
    )


async def delete_document(pool: asyncpg.Pool, document_id: int, user_id: int) -> tuple[bool, str | None]:
    """Delete the user's own document. Return (deleted, title of the old version that works again).

    The old version this document replaced takes its place: it works again if this document was
    active, or it is now replaced by whatever replaced this document.
    """
    async with pool.acquire() as connection:
        async with connection.transaction():
            # FOR UPDATE locks the row: two quick presses of the button cannot delete it twice
            document = await connection.fetchrow(
                "SELECT replaced_by FROM documents WHERE id = $1 AND uploader_id = $2 FOR UPDATE",
                document_id, user_id,
            )
            if document is None:
                return False, None

            newer_id = document["replaced_by"]
            restored_title = await connection.fetchval(
                """
                UPDATE documents SET replaced_by = $2, is_active = ($2::integer IS NULL)
                WHERE replaced_by = $1
                RETURNING title
                """,
                document_id, newer_id,
            )
            await connection.execute("DELETE FROM documents WHERE id = $1", document_id)

    # The old version works again only if the deleted document was not replaced itself
    return True, restored_title if newer_id is None else None


async def document_exists(pool: asyncpg.Pool, file_hash: str) -> bool:
    return await pool.fetchval("SELECT EXISTS(SELECT 1 FROM documents WHERE file_hash = $1)", file_hash)


async def add_document(
    pool: asyncpg.Pool,
    *,
    title: str,
    place: str,
    chunks: Sequence[Chunk],
    file_hash: str,
    telegram_file_id: str | None = None,
    archive_path: str | None = None,
    uploader_id: int | None = None,
    uploader_name: str | None = None,
    replaces: int | None = None,
) -> int:
    """Insert a document with its parts and return its id.

    replaces: id of the old version that stops being used in answers.
    """
    topics = all_topics(chunks)

    # One transaction: either everything is saved, or nothing (no document without parts)
    async with pool.acquire() as connection:
        async with connection.transaction():
            document_id = await connection.fetchval(
                """
                INSERT INTO documents
                    (title, place, topics, file_hash, telegram_file_id, archive_path, uploader_id, uploader_name)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                title, place, topics, file_hash, telegram_file_id, archive_path, uploader_id, uploader_name,
            )
            await connection.executemany(
                "INSERT INTO chunks (document_id, position, content, topics) VALUES ($1, $2, $3, $4)",
                [(document_id, position, chunk.content, chunk.topics) for position, chunk in enumerate(chunks)],
            )
            if replaces is not None:
                await connection.execute(
                    "UPDATE documents SET is_active = FALSE, replaced_by = $1 WHERE id = $2",
                    document_id, replaces,
                )
    return document_id
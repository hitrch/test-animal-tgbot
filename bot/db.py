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
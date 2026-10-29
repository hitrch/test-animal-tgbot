"""Shared fixtures: a clean test database for the SQL tests."""
import asyncio
import os

import asyncpg
import pytest

from bot.db import init_schema

# A separate database (the db-test service in docker-compose.yml), never the bot's one:
# every test drops all tables
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://test:test@127.0.0.1:5433/test")
SKIP_REASON = "test database is not running: docker compose --profile test up -d db-test"

# Set after the first failed connection, so without the database the SQL tests are skipped quickly
database_missing = False


@pytest.fixture
async def pool():
    global database_missing
    if database_missing:
        pytest.skip(SKIP_REASON)
    try:
        pool = await asyncpg.create_pool(TEST_DATABASE_URL, timeout=3)
    except (OSError, asyncio.TimeoutError, asyncpg.PostgresError):
        database_missing = True
        pytest.skip(SKIP_REASON)

    # Every test starts with empty tables created by the same schema.sql as the bot uses
    await pool.execute("DROP TABLE IF EXISTS chunks, documents, consents")
    await init_schema(pool)
    yield pool
    await pool.close()
import asyncio
import logging

import asyncpg
from aiogram import Bot, Dispatcher

from bot.archive import load_archive
from bot.config import load_config
from bot.db import init_schema
from bot.handlers import consent, questions
from bot.llm import LLM
from bot.middlewares import AccessMiddleware

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()

    # Check the database connection on startup so problems show up immediately
    pool = await asyncpg.create_pool(config.database_url)
    logger.info("Connected to Postgres")
    await init_schema(pool)
    logger.info("Database schema is ready")
    await load_archive(pool)

    bot = Bot(config.telegram_token)
    dp = Dispatcher()
    # Every handler that has a "pool" or "llm" parameter gets these objects automatically
    dp["pool"] = pool
    dp["llm"] = LLM(config.groq_api_key, config.parse_model, config.answer_model)
    # Outer middleware runs for every message, even if no handler matches it
    dp.message.outer_middleware(AccessMiddleware())
    dp.include_router(consent.router)
    # "Any text" handler goes last, after all more specific handlers
    dp.include_router(questions.router)

    try:
        logger.info("Bot started")
        await dp.start_polling(bot)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
import asyncio
import logging

import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from bot import texts
from bot.archive import load_archive
from bot.config import load_config
from bot.db import init_schema
from bot.handlers import consent, documents, my_documents, questions
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
    # FSM data (an upload waiting for a button) is kept in memory by default (MemoryStorage)
    dp = Dispatcher()
    # Every handler that has a "pool" or "llm" parameter gets these objects automatically
    dp["pool"] = pool
    dp["llm"] = LLM(config.groq_api_key, config.parse_model, config.answer_model)
    # Outer middleware runs for every message, even if no handler matches it
    dp.message.outer_middleware(AccessMiddleware())
    dp.include_router(consent.router)
    dp.include_router(documents.router)
    dp.include_router(my_documents.router)
    # "Any text" handler goes last, after all more specific handlers
    dp.include_router(questions.router)

    # The command menu in Telegram (the "/" button) is set from the code, so it always matches the handlers
    await bot.set_my_commands([
        BotCommand(command="start", description=texts.COMMAND_START),
        BotCommand(command="help", description=texts.COMMAND_HELP),
        BotCommand(command="my", description=texts.COMMAND_MY),
    ])

    try:
        logger.info("Bot started")
        await dp.start_polling(bot)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
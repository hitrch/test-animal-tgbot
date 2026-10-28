import os
from dataclasses import dataclass


@dataclass
class Config:
    telegram_token: str
    groq_api_key: str
    database_url: str
    answer_model: str
    parse_model: str


def load_config() -> Config:
    """Read settings from environment variables. Fail early if a required one is missing."""
    missing = [name for name in ("TELEGRAM_BOT_TOKEN", "GROQ_API_KEY", "DATABASE_URL") if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}. See .env.example")

    return Config(
        telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
        groq_api_key=os.environ["GROQ_API_KEY"],
        database_url=os.environ["DATABASE_URL"],
        answer_model=os.getenv("GROQ_ANSWER_MODEL", "openai/gpt-oss-120b"),
        parse_model=os.getenv("GROQ_PARSE_MODEL", "openai/gpt-oss-20b"),
    )
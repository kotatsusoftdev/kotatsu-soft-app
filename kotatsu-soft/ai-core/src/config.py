import os


def _get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


class Settings:
    DISCORD_TOKEN: str = _get_env("DISCORD_TOKEN")
    MUCHABURI_CHANNEL_ID: str = _get_env("MUCHABURI_CHANNEL_ID")
    GEMINI_API_KEY: str = _get_env("GEMINI_API_KEY")
    OPENAI_API_KEY: str = _get_env("OPENAI_API_KEY")


settings = Settings()

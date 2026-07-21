import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


class ConfigError(ValueError):
    pass


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ConfigError(f"[config] 環境変数 '{key}' が設定されていません。.env を確認してください。")
    return value


def _validate_int(value: str, key: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f"[config] 環境変数 '{key}' の値が正しい整数ではありません: {value}")


@dataclass(frozen=True)
class Config:
    DISCORD_TOKEN: str
    MUCHABURI_CHANNEL_ID: int
    GEMINI_API_KEY: str
    MEETING_CHANNEL_ID: int
    PRESIDENT_MENTION: str

    @classmethod
    def load(cls) -> "Config":
        discord_token = _require_env("DISCORD_TOKEN")
        muchaburi_channel_id = _require_env("MUCHABURI_CHANNEL_ID")
        gemini_api_key = _require_env("GEMINI_API_KEY")
        meeting_channel_id = _require_env("MEETING_CHANNEL_ID")
        president_mention = _require_env("PRESIDENT_MENTION")
        return cls(
            DISCORD_TOKEN=discord_token,
            MUCHABURI_CHANNEL_ID=_validate_int(muchaburi_channel_id, "MUCHABURI_CHANNEL_ID"),
            GEMINI_API_KEY=gemini_api_key,
            MEETING_CHANNEL_ID=_validate_int(meeting_channel_id, "MEETING_CHANNEL_ID"),
            PRESIDENT_MENTION=president_mention,
        )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.load()
    return _config

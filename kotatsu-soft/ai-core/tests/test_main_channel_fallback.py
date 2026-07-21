from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import main
from config import Config


class FakeTextChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id


class FakeSourceChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.messages: list[str] = []

    async def send(self, content: str, **kwargs: Any) -> None:
        self.messages.append(content)


@pytest.mark.asyncio
async def test_on_message_fetches_channel_when_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)

    source_channel = FakeSourceChannel(cfg.MUCHABURI_CHANNEL_ID)
    fetched_channel = FakeTextChannel(cfg.MEETING_CHANNEL_ID)
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        channel=source_channel,
        content="new game idea",
    )

    fetch_mock = AsyncMock(return_value=fetched_channel)
    run_meeting_mock = AsyncMock()
    process_commands_mock = AsyncMock()

    monkeypatch.setattr(main.discord, "TextChannel", FakeTextChannel)
    monkeypatch.setattr(main.bot, "get_channel", lambda _channel_id: None)
    monkeypatch.setattr(main.bot, "fetch_channel", fetch_mock)
    monkeypatch.setattr(main.bot, "process_commands", process_commands_mock)
    monkeypatch.setattr(main, "run_meeting_round", run_meeting_mock)
    monkeypatch.setattr(main, "_try_reserve_meeting_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "_release_meeting_channel", AsyncMock())

    await main.on_message(message)

    fetch_mock.assert_awaited_once_with(cfg.MEETING_CHANNEL_ID)
    run_meeting_mock.assert_awaited_once_with("new game idea", fetched_channel)
    process_commands_mock.assert_awaited_once_with(message)
    assert source_channel.messages[0].startswith("📥 了解しました")

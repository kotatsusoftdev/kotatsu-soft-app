from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import main
from config import Config


class FakeTextChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.messages: list[Any] = []

    async def send(self, content: str, **kwargs: Any) -> None:
        self.messages.append({"content": content, "kwargs": kwargs})


class FakeSourceChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.messages: list[Any] = []

    async def send(self, content: str, **kwargs: Any) -> None:
        self.messages.append({"content": content, "kwargs": kwargs})


@pytest.mark.asyncio
async def test_on_message_shows_process_select_view(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)

    source_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        channel=source_channel,
        content="何か指示",
    )

    run_meeting_mock = AsyncMock()
    process_commands_mock = AsyncMock()
    monkeypatch.setattr(main, "run_meeting_round", run_meeting_mock)
    monkeypatch.setattr(main.bot, "process_commands", process_commands_mock)

    await main.on_message(message)

    run_meeting_mock.assert_not_awaited()
    process_commands_mock.assert_awaited_once_with(message)
    assert source_channel.messages
    first = source_channel.messages[0]
    assert first["content"] == "どのプロセスを開始しますか？"
    assert "view" in first["kwargs"]
    assert isinstance(first["kwargs"]["view"], main.ProcessSelectView)


@pytest.mark.asyncio
async def test_start_meeting_from_theme_posts_ack_to_planning_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)

    order_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)
    planning_channel = FakeTextChannel(cfg.MEETING_CHANNEL_ID)

    fetch_mock = AsyncMock(return_value=planning_channel)
    run_meeting_mock = AsyncMock()

    monkeypatch.setattr(main.discord, "TextChannel", FakeTextChannel)
    monkeypatch.setattr(main.bot, "get_channel", lambda _channel_id: None)
    monkeypatch.setattr(main.bot, "fetch_channel", fetch_mock)
    monkeypatch.setattr(main, "run_meeting_round", run_meeting_mock)
    monkeypatch.setattr(main, "_try_reserve_meeting_channel", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "_release_meeting_channel", AsyncMock())

    await main.start_meeting_from_theme("new game idea", order_channel=order_channel)

    fetch_mock.assert_awaited_once_with(cfg.MEETING_CHANNEL_ID)
    run_meeting_mock.assert_awaited_once()
    assert run_meeting_mock.await_args.args[0] == "new game idea"
    assert run_meeting_mock.await_args.args[1] is planning_channel
    assert run_meeting_mock.await_args.kwargs["theme_parts"].title == "new game idea"
    assert order_channel.messages == []
    assert any(
        str(item["content"]).startswith("📥 了解しました")
        for item in planning_channel.messages
    )
    assert any("new game idea" in str(item["content"]) for item in planning_channel.messages)

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import main
from config import Config
from theme_proposal import ThemeProposalError


class FakeSourceChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.messages: list[Any] = []

    async def send(self, content: str, **kwargs: Any) -> None:
        self.messages.append({"content": content, "kwargs": kwargs})


@pytest.mark.asyncio
async def test_propose_theme_options_for_meeting_posts_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_active_meeting_channel_ids", set())

    meeting_channel = FakeSourceChannel(cfg.MEETING_CHANNEL_ID)
    monkeypatch.setattr(
        main, "resolve_meeting_channel", AsyncMock(return_value=meeting_channel)
    )

    payload = {
        "researcher": "ヂャイアン",
        "options": [
            {
                "option_id": 1,
                "title": "案1",
                "concept_summary": "要約1",
                "combined_sources": {
                    "trend_id": "t1",
                    "mechanic_id": "m1",
                    "trend_label": "トレンドA",
                    "mechanic_label": "メカニクスA",
                },
                "viral_point": "バズ1",
            },
            {
                "option_id": 2,
                "title": "案2",
                "concept_summary": "要約2",
                "combined_sources": {
                    "trend_id": "t2",
                    "mechanic_id": "m2",
                    "trend_label": "トレンドB",
                    "mechanic_label": "メカニクスB",
                },
                "viral_point": "バズ2",
            },
            {
                "option_id": 3,
                "title": "案3",
                "concept_summary": "要約3",
                "combined_sources": {
                    "trend_id": "t3",
                    "mechanic_id": "m3",
                    "trend_label": "トレンドC",
                    "mechanic_label": "メカニクスC",
                },
                "viral_point": "バズ3",
            },
        ],
    }

    class FakeProposer:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def generate(self, previous_titles=None):
            return payload

    monkeypatch.setattr(main, "ThemeProposer", FakeProposer)
    monkeypatch.setattr(main, "_post_as_agent", AsyncMock())

    order_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)
    await main.propose_theme_options_for_meeting(order_channel=order_channel, cfg=cfg)

    assert any("テーマ案を確認" in m["content"] for m in order_channel.messages)
    assert any("作成中" in m["content"] for m in meeting_channel.messages)
    assert any("企画テーマ案" in m["content"] for m in meeting_channel.messages)
    view_msgs = [m for m in meeting_channel.messages if "view" in m["kwargs"]]
    assert len(view_msgs) == 1
    view = view_msgs[0]["kwargs"]["view"]
    assert isinstance(view, main.ThemeOptionSelectView)
    assert view.meeting_channel_id == cfg.MEETING_CHANNEL_ID
    assert cfg.MEETING_CHANNEL_ID in main._active_meeting_channel_ids


@pytest.mark.asyncio
async def test_propose_theme_options_releases_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_active_meeting_channel_ids", set())

    meeting_channel = FakeSourceChannel(cfg.MEETING_CHANNEL_ID)
    monkeypatch.setattr(
        main, "resolve_meeting_channel", AsyncMock(return_value=meeting_channel)
    )

    class BoomProposer:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def generate(self, previous_titles=None):
            raise ThemeProposalError("調査データなし")

    monkeypatch.setattr(main, "ThemeProposer", BoomProposer)

    order_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)
    await main.propose_theme_options_for_meeting(order_channel=order_channel, cfg=cfg)

    assert any("失敗しました" in m["content"] for m in meeting_channel.messages)
    assert cfg.MEETING_CHANNEL_ID not in main._active_meeting_channel_ids


@pytest.mark.asyncio
async def test_propose_theme_options_busy_when_reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(
        main, "_active_meeting_channel_ids", {cfg.MEETING_CHANNEL_ID}
    )
    meeting_channel = FakeSourceChannel(cfg.MEETING_CHANNEL_ID)
    monkeypatch.setattr(
        main, "resolve_meeting_channel", AsyncMock(return_value=meeting_channel)
    )

    order_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)
    await main.propose_theme_options_for_meeting(order_channel=order_channel, cfg=cfg)

    assert any("すでに進行中" in m["content"] for m in order_channel.messages)
    assert len(meeting_channel.messages) == 0


def test_format_theme_options_agent_message() -> None:
    text = main.format_theme_options_agent_message(
        {
            "options": [
                {
                    "option_id": 1,
                    "title": "自撮り崩壊",
                    "concept_summary": "積んで崩す",
                    "viral_point": "短尺映え",
                    "combined_sources": {
                        "trend_label": "全肯定",
                        "mechanic_label": "タワー",
                    },
                }
            ]
        }
    )
    assert "ヂャイアン" in text or "オレ様" in text
    assert "案1: 自撮り崩壊" in text
    assert "積んで崩す" in text
    assert "全肯定 × タワー" in text


@pytest.mark.asyncio
async def test_theme_option_abort_releases_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_active_meeting_channel_ids", {cfg.MEETING_CHANNEL_ID})

    view = main.ThemeOptionSelectView(
        options=[{"option_id": 1, "title": "案1"}],
        api_key="dummy",
        meeting_channel_id=cfg.MEETING_CHANNEL_ID,
    )

    class FakeMessage:
        async def edit(self, **kwargs: Any) -> None:
            return None

    interaction = SimpleNamespace(
        response=SimpleNamespace(send_message=AsyncMock()),
        message=FakeMessage(),
    )
    await view._on_abort(interaction)
    assert cfg.MEETING_CHANNEL_ID not in main._active_meeting_channel_ids
    assert view._holds_reservation is False


@pytest.mark.asyncio
async def test_start_meeting_from_theme_already_reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_active_meeting_channel_ids", {cfg.MEETING_CHANNEL_ID})
    meeting_channel = FakeSourceChannel(cfg.MEETING_CHANNEL_ID)
    order_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)

    run_called = {"value": False}

    async def fake_run_meeting_round(theme: str, channel: Any) -> None:
        run_called["value"] = True
        assert "選択テーマ" in theme
        assert channel.id == cfg.MEETING_CHANNEL_ID

    monkeypatch.setattr(main, "run_meeting_round", fake_run_meeting_round)

    await main.start_meeting_from_theme(
        "選択テーマ\n\n詳細",
        order_channel=order_channel,
        already_reserved=True,
        meeting_channel=meeting_channel,
    )

    assert run_called["value"] is True
    assert any("了解しました" in m["content"] for m in meeting_channel.messages)
    assert cfg.MEETING_CHANNEL_ID not in main._active_meeting_channel_ids

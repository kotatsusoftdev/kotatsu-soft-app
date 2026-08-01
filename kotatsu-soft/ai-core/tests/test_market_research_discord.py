from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import main
from config import Config


class FakeSourceChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.messages: list[Any] = []

    async def send(self, content: str, **kwargs: Any) -> None:
        self.messages.append({"content": content, "kwargs": kwargs})


@pytest.mark.asyncio
async def test_propose_market_research_posts_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_market_research_running", False)

    results_channel = FakeSourceChannel(cfg.MARKET_RESEARCH_CHANNEL_ID)
    monkeypatch.setattr(
        main,
        "resolve_market_research_channel",
        AsyncMock(return_value=results_channel),
    )

    order_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)
    await main.propose_market_research(order_channel=order_channel, cfg=cfg)

    assert len(order_channel.messages) == 1
    assert f"<#{cfg.MARKET_RESEARCH_CHANNEL_ID}>" in order_channel.messages[0]["content"]
    assert "確認してください" in order_channel.messages[0]["content"]
    assert "view" not in order_channel.messages[0]["kwargs"]

    assert len(results_channel.messages) == 1
    proposal = results_channel.messages[0]
    assert "市場調査" in proposal["content"]
    assert "view" in proposal["kwargs"]
    view = proposal["kwargs"]["view"]
    assert isinstance(view, main.MarketResearchConfirmView)
    assert view.results_channel_id == cfg.MARKET_RESEARCH_CHANNEL_ID


@pytest.mark.asyncio
async def test_propose_market_research_reports_missing_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_market_research_running", False)
    monkeypatch.setattr(
        main, "resolve_market_research_channel", AsyncMock(return_value=None)
    )

    order_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)
    await main.propose_market_research(order_channel=order_channel, cfg=cfg)
    assert "市場調査チャンネルが見つかりません" in order_channel.messages[0]["content"]


def test_format_market_research_summary_includes_counts() -> None:
    trends = {
        "data_source": "Yahoo! Realtime + Togetter + Tavily API",
        "trends": [
            {
                "sns_platform": "TikTok",
                "viral_score": 90,
                "keyword": {
                    "original": "今日ビジュいいじゃん音源",
                    "abstracted": "全肯定セルフ褒め",
                },
            }
        ],
    }
    mechanics = {
        "total_count": 3,
        "mechanics": [
            {
                "name": "千羽鶴量産クリッカー",
                "inspiration": {
                    "unique_motif": "千羽鶴の数字インフレ",
                },
            }
        ],
    }
    summary = main.format_market_research_summary(trends, mechanics)
    assert "トレンド: 1件" in summary
    assert "合計 3件" in summary
    assert "全肯定セルフ褒め" in summary
    assert "千羽鶴" in summary
    assert "ヂャイアン" in summary or "ぶちかまし" in summary


@pytest.mark.asyncio
async def test_publish_market_research_results_posts_as_jaian(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict[str, str]] = []

    class FakeChannel:
        id = 444

        async def send(self, content: str, **kwargs: Any) -> None:
            posted.append({"username": "bot", "content": content})

    async def fake_post_as_agent(channel, *, username: str, avatar_url: str, content: str):
        posted.append(
            {"username": username, "avatar_url": avatar_url, "content": content}
        )

    monkeypatch.setattr(main, "_post_as_agent", fake_post_as_agent)

    trends = {
        "data_source": "test",
        "trends": [
            {
                "sns_platform": "X",
                "viral_score": 80,
                "keyword": {"original": "a", "abstracted": "b"},
            }
        ],
    }
    mechanics = {"total_count": 1, "mechanics": [{"name": "テスト"}]}
    await main.publish_market_research_results(FakeChannel(), trends, mechanics)

    assert posted[0]["username"] == "bot"
    assert "市場調査が完了" in posted[0]["content"]
    assert posted[1]["username"] == "ヂャイアン(マーケ)"
    assert posted[1]["avatar_url"] == main._AGENT_AVATAR_URLS["marketing"]
    assert "トレンド: 1件" in posted[1]["content"]


@pytest.mark.asyncio
async def test_market_research_confirm_start_runs_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_market_research_running", False)

    results_channel = FakeSourceChannel(cfg.MARKET_RESEARCH_CHANNEL_ID)
    monkeypatch.setattr(
        main,
        "resolve_text_channel",
        AsyncMock(return_value=results_channel),
    )

    published: list[tuple] = []

    async def fake_publish(channel, trends, mechanics):
        published.append((channel, trends, mechanics))

    monkeypatch.setattr(main, "publish_market_research_results", fake_publish)

    class FakeResearcher:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run_all(self) -> tuple[dict, dict]:
            return (
                {"data_source": "mock", "trends": [{"keyword": {"abstracted": "x"}}]},
                {"total_count": 2, "mechanics": [{"name": "y"}]},
            )

    monkeypatch.setattr(main, "MarketResearcher", FakeResearcher)

    view = main.MarketResearchConfirmView(
        api_key="dummy",
        results_channel_id=cfg.MARKET_RESEARCH_CHANNEL_ID,
    )

    followups: list[str] = []
    responses: list[str] = []

    class FakeInteraction:
        message = None

        class response:
            @staticmethod
            async def send_message(content: str, **kwargs: Any) -> None:
                responses.append(content)

        class followup:
            @staticmethod
            async def send(content: str, **kwargs: Any) -> None:
                followups.append(content)

    await view._on_start(FakeInteraction())  # type: ignore[arg-type]

    assert any("市場調査中" in msg for msg in responses)
    assert len(published) == 1
    assert published[0][0] is results_channel
    assert published[0][1]["trends"]
    assert published[0][2]["total_count"] == 2
    assert main._market_research_running is False
    assert followups == []


def test_process_select_includes_market_research_button() -> None:
    import discord

    view = main.ProcessSelectView()
    labels = [
        item.label
        for item in view.children
        if isinstance(item, discord.ui.Button)
    ]
    assert "企画会議" in labels
    assert "反省会" in labels
    assert "市場調査" in labels

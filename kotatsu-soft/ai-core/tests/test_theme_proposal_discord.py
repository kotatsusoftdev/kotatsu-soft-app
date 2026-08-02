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

        def generate(self, **kwargs: Any):
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

        def generate(self, **kwargs: Any):
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


def test_format_theme_options_agent_messages_splits_per_option() -> None:
    messages = main.format_theme_options_agent_messages(
        {
            "options": [
                {
                    "option_id": 1,
                    "title": "自撮り崩壊",
                    "concept_summary": "積んで崩す",
                    "approach_type": "元ネタ直球",
                    "design_intent": "絵面が完成しているので直球",
                    "synergy_reason": "自己肯定を積む操作で増幅",
                    "viral_point": "短尺映え",
                    "combined_sources": {
                        "trend_label": "全肯定",
                        "mechanic_label": "タワー",
                    },
                },
                {
                    "option_id": 2,
                    "title": "大喜利崩壊",
                    "concept_summary": "回して笑う",
                    "approach_type": "世界観置換",
                    "design_intent": "別世界観の方がカオス",
                    "synergy_reason": "理不尽をお題で回す",
                    "viral_point": "一言映え",
                    "combined_sources": {
                        "trend_label": "法則",
                        "mechanic_label": "ルーレット",
                    },
                },
            ]
        }
    )
    assert len(messages) == 3
    assert "オレ様" in messages[0]
    assert "[ 案 1 ]" in messages[1]
    assert "自撮り崩壊" in messages[1]
    assert "元ネタ直球案" in messages[1]
    assert "コンセプト" in messages[1]
    assert "積んで崩す" in messages[1]
    assert "アプローチの狙い" in messages[1]
    assert "絵面が完成しているので直球" in messages[1]
    assert "シナジー理由" in messages[1]
    assert "自己肯定を積む操作で増幅" in messages[1]
    assert "トレンド(全肯定) × ゲーム性(タワー)" in messages[1]
    assert "[ 案 2 ]" in messages[2]
    assert "大喜利崩壊" in messages[2]
    assert "世界観置換案" in messages[2]
    assert "案 1" not in messages[2]


@pytest.mark.asyncio
async def test_publish_theme_options_posts_jaian_before_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict[str, Any]] = []

    class FakeChannel:
        id = 999

        async def send(self, content: str, **kwargs: Any) -> None:
            posted.append(
                {
                    "kind": "bot",
                    "content": content,
                    "has_view": "view" in kwargs,
                }
            )

    async def fake_post_as_agent(channel, *, username: str, avatar_url: str, content: str):
        posted.append(
            {
                "kind": "agent",
                "username": username,
                "content": content,
            }
        )

    monkeypatch.setattr(main, "_post_as_agent", fake_post_as_agent)

    payload = {
        "options": [
            {
                "option_id": 1,
                "title": "案1",
                "concept_summary": "要約1",
                "synergy_reason": "理由1",
                "viral_point": "バズ1",
                "combined_sources": {
                    "trend_label": "A",
                    "mechanic_label": "B",
                },
            },
            {
                "option_id": 2,
                "title": "案2",
                "concept_summary": "要約2",
                "synergy_reason": "理由2",
                "viral_point": "バズ2",
                "combined_sources": {
                    "trend_label": "C",
                    "mechanic_label": "D",
                },
            },
        ]
    }
    await main.publish_theme_options(FakeChannel(), payload, api_key="dummy")

    agent_posts = [p for p in posted if p["kind"] == "agent"]
    bot_posts = [p for p in posted if p["kind"] == "bot"]
    assert len(agent_posts) == 3  # intro + 2 options
    assert all(p["username"] == "ヂャイアン(マーケ)" for p in agent_posts)
    assert "案1" in agent_posts[1]["content"]
    assert "シナジー理由" in agent_posts[1]["content"]
    assert "案2" in agent_posts[2]["content"]
    assert len(bot_posts) == 1
    assert bot_posts[0]["has_view"] is True
    assert posted.index(agent_posts[-1]) < posted.index(bot_posts[0])


@pytest.mark.asyncio
async def test_theme_option_regen_passes_accumulated_previous_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_active_meeting_channel_ids", {cfg.MEETING_CHANNEL_ID})

    meeting_channel = FakeSourceChannel(cfg.MEETING_CHANNEL_ID)
    monkeypatch.setattr(
        main, "resolve_text_channel", AsyncMock(return_value=meeting_channel)
    )

    captured: dict[str, Any] = {}

    async def fake_propose(**kwargs: Any) -> None:
        captured["previous_options"] = kwargs.get("previous_options")

    monkeypatch.setattr(main, "propose_theme_options_for_meeting", fake_propose)

    history_opt = {
        "option_id": 9,
        "title": "履歴案",
        "combined_sources": {"trend_id": "t0", "mechanic_id": "m0"},
    }
    current_opt = {
        "option_id": 1,
        "title": "現行案",
        "combined_sources": {"trend_id": "t1", "mechanic_id": "m1"},
    }
    view = main.ThemeOptionSelectView(
        options=[current_opt],
        api_key="dummy",
        meeting_channel_id=cfg.MEETING_CHANNEL_ID,
        avoid_history=[history_opt],
    )

    class FakeMessage:
        async def edit(self, **kwargs: Any) -> None:
            return None

    interaction = SimpleNamespace(
        response=SimpleNamespace(send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        message=FakeMessage(),
    )
    await view._on_regen(interaction)

    assert captured["previous_options"] == [history_opt, current_opt]


@pytest.mark.asyncio
async def test_publish_theme_options_keeps_avoid_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted_views: list[Any] = []

    class FakeChannel:
        id = 123

        async def send(self, content: str, **kwargs: Any) -> None:
            if "view" in kwargs:
                posted_views.append(kwargs["view"])

    monkeypatch.setattr(main, "_post_as_agent", AsyncMock())

    history = [{"title": "過去", "combined_sources": {"trend_id": "t", "mechanic_id": "m"}}]
    await main.publish_theme_options(
        FakeChannel(),
        {
            "options": [
                {
                    "option_id": 1,
                    "title": "新案",
                    "concept_summary": "要約",
                    "synergy_reason": "理由",
                    "viral_point": "バズ",
                    "combined_sources": {
                        "trend_label": "A",
                        "mechanic_label": "B",
                    },
                }
            ]
        },
        api_key="dummy",
        avoid_history=history,
    )
    assert len(posted_views) == 1
    assert posted_views[0].avoid_history == history


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

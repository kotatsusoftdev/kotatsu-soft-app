from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import main
from config import Config
from post_mortem import LessonUpdate


class FakeSourceChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.messages: list[Any] = []

    async def send(self, content: str, **kwargs: Any) -> None:
        self.messages.append({"content": content, "kwargs": kwargs})


@pytest.mark.asyncio
async def test_legacy_post_mortem_channel_does_not_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main.bot, "process_commands", AsyncMock())

    source_channel = FakeSourceChannel(999999)
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        channel=source_channel,
        content="反省会お願い",
    )

    await main.on_message(message)
    assert source_channel.messages == []


@pytest.mark.asyncio
async def test_propose_post_mortem_posts_proposal_to_post_mortem_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_post_mortem_running", False)
    monkeypatch.setattr(
        main,
        "get_latest_linked_game",
        lambda: {
            "game_id": "matatabi_chaos",
            "game_title": "マタタビ大合唱",
            "artifact_stem": "demo_stem",
            "game_path": "game-projects/001_matatabi_chaos/src/index.html",
        },
    )

    results_channel = FakeSourceChannel(cfg.POST_MORTEM_CHANNEL_ID)
    monkeypatch.setattr(
        main,
        "resolve_post_mortem_channel",
        AsyncMock(return_value=results_channel),
    )

    order_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)
    await main.propose_post_mortem(order_channel=order_channel, cfg=cfg)

    assert len(order_channel.messages) == 1
    assert f"<#{cfg.POST_MORTEM_CHANNEL_ID}>" in order_channel.messages[0]["content"]
    assert "確認してください" in order_channel.messages[0]["content"]
    assert "view" not in order_channel.messages[0]["kwargs"]

    assert len(results_channel.messages) == 1
    proposal = results_channel.messages[0]
    assert "matatabi_chaos" in proposal["content"]
    assert "demo_stem" in proposal["content"]
    assert "view" in proposal["kwargs"]
    view = proposal["kwargs"]["view"]
    assert view.results_channel_id == cfg.POST_MORTEM_CHANNEL_ID


@pytest.mark.asyncio
async def test_propose_post_mortem_reports_missing_results_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_post_mortem_running", False)
    monkeypatch.setattr(main, "resolve_post_mortem_channel", AsyncMock(return_value=None))

    order_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)
    await main.propose_post_mortem(order_channel=order_channel, cfg=cfg)
    assert "反省会チャンネルが見つかりません" in order_channel.messages[0]["content"]


@pytest.mark.asyncio
async def test_propose_post_mortem_reports_missing_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config.load()
    monkeypatch.setattr(main, "_config", cfg)
    monkeypatch.setattr(main, "_post_mortem_running", False)
    monkeypatch.setattr(main, "get_latest_linked_game", lambda: None)

    results_channel = FakeSourceChannel(cfg.POST_MORTEM_CHANNEL_ID)
    monkeypatch.setattr(
        main,
        "resolve_post_mortem_channel",
        AsyncMock(return_value=results_channel),
    )

    order_channel = FakeSourceChannel(cfg.PRESIDENT_ORDER_CHANNEL_ID)
    await main.propose_post_mortem(order_channel=order_channel, cfg=cfg)
    assert "紐づいたゲームがありません" in order_channel.messages[0]["content"]
    assert results_channel.messages == []


def test_build_and_format_post_mortem_messages() -> None:
    latest = {
        "game_id": "matatabi_chaos",
        "game_title": "マタタビ大合唱",
        "artifact_stem": "stem_1",
    }
    proposal = main.build_post_mortem_proposal_message(latest)
    assert "matatabi_chaos" in proposal
    assert "stem_1" in proposal

    system_messages = main.format_post_mortem_system_messages(["review missing"])
    assert any("反省会が完了" in msg for msg in system_messages)
    assert any("review missing" in msg for msg in system_messages)

    update = LessonUpdate(
        role="pm",
        name="すずかちゃん(PM)",
        before=["旧1", "旧2", "旧3"],
        after=["新1", "新2", "新3"],
        path=Path("agents/pm/lessons_learned.yaml"),
        speech="着地判定がずれていたから、次の教訓を得たよ。\n1. 新1\n2. 新2\n3. 新3",
    )
    agent_message = main.format_post_mortem_agent_message(update)
    assert "着地判定がずれていた" in agent_message
    assert "新1" in agent_message
    assert "before" not in agent_message
    assert "after" not in agent_message

    fallback = main.format_post_mortem_agent_message(
        LessonUpdate(
            role="pm",
            name="すずかちゃん(PM)",
            before=["旧1"],
            after=["新A", "新B", "新C"],
            path=Path("agents/pm/lessons_learned.yaml"),
            speech="",
        )
    )
    assert "次の教訓を得たよ" in fallback
    assert "新A" in fallback


@pytest.mark.asyncio
async def test_publish_post_mortem_results_posts_as_each_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict[str, str]] = []

    class FakeChannel:
        id = 123

        async def send(self, content: str, **kwargs: Any) -> None:
            posted.append({"username": "bot", "content": content})

    async def fake_post_as_agent(channel, *, username: str, avatar_url: str, content: str):
        posted.append({"username": username, "avatar_url": avatar_url, "content": content})

    monkeypatch.setattr(main, "_post_as_agent", fake_post_as_agent)

    updates = [
        LessonUpdate(
            role="pm",
            name="すずかちゃん(PM)",
            before=["旧PM"],
            after=["新PM"],
            path=Path("agents/pm/lessons_learned.yaml"),
            speech="判定のズレがあったから、次の教訓を得たの。新PMだよ。",
        ),
        LessonUpdate(
            role="dev",
            name="スゴ杉くん(エンジニア)",
            before=["旧Dev"],
            after=["新Dev"],
            path=Path("agents/dev/lessons_learned.yaml"),
            speech="実装のずれがあったため、次の教訓を得ました。新Devです。",
        ),
        LessonUpdate(
            role="marketing",
            name="ヂャイアン(マーケ)",
            before=["旧Mkt"],
            after=["新Mkt"],
            path=Path("agents/marketing/lessons_learned.yaml"),
            speech="見た目がしょぼかったから、オレは次の教訓を得たぜ！新Mktだ！",
        ),
    ]

    await main.publish_post_mortem_results(FakeChannel(), updates, [])

    assert posted[0]["username"] == "bot"
    assert posted[1]["username"] == "すずかちゃん(PM)"
    assert posted[2]["username"] == "スゴ杉くん(エンジニア)"
    assert posted[3]["username"] == "ヂャイアン(マーケ)"
    assert "判定のズレがあった" in posted[1]["content"]
    assert "新PM" in posted[1]["content"]
    assert "実装のずれがあった" in posted[2]["content"]
    assert "オレは次の教訓を得たぜ" in posted[3]["content"]
    assert posted[1]["avatar_url"] == main._AGENT_AVATAR_URLS["pm"]
    assert posted[2]["avatar_url"] == main._AGENT_AVATAR_URLS["dev"]
    assert posted[3]["avatar_url"] == main._AGENT_AVATAR_URLS["marketing"]

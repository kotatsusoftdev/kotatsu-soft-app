from typing import Any
from pathlib import Path

import pytest

from agents.dev.agent import DevAgent
from agents.pm.schemas import PMDecision
from orchestrator import DynamicOrchestrator, build_president_final_message


class StubPM:
    name = "PM"
    avatar_url = "https://example.com/pm.png"

    def __init__(self, decisions: list[PMDecision | Exception]):
        self._decisions = list(decisions)

    async def decide_next_step(self, *args: Any, **kwargs: Any) -> PMDecision:
        if not self._decisions:
            raise RuntimeError("No stub decisions left")
        next_item = self._decisions.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


class StubAgent:
    def __init__(self, name: str, reply: str):
        self.name = name
        self.avatar_url = "https://example.com/agent.png"
        self.reply = reply
        self.calls: list[tuple[str, list[str]]] = []

    async def think_and_reply(self, prompt: str, conversation_history: list[str]) -> str:
        self.calls.append((prompt, list(conversation_history)))
        return self.reply


class StubChannel:
    def __init__(self):
        self.id = 999
        self.messages: list[str] = []

    async def send(self, content: str, **kwargs: Any) -> None:
        self.messages.append(content)


def make_decision(
    *,
    speech: str,
    phase: str,
    next_action: str,
    target_agent: str | None = None,
    instruction_for_target: str | None = None,
) -> PMDecision:
    return PMDecision(
        speech=speech,
        phase=phase,
        next_action=next_action,
        target_agent=target_agent,
        instruction_for_target=instruction_for_target,
        final_recommendation="best plan",
        final_category="category",
        revision_guidance="guidance",
    )


def test_build_president_final_message_uses_pmdecision_fields() -> None:
    decision = make_decision(
        speech="pm final",
        phase="FINAL",
        next_action="FINISH_FOR_PRESIDENT",
    )

    message = build_president_final_message("@社長", decision)

    assert "PM最終発言" not in message
    assert "直近の議論履歴" not in message
    assert message == (
        "---\n"
        "@社長 最終提案を提示しますね！\n\n"
        "**【社長への最終提案】**\n"
        "・**カテゴリ:** category\n"
        "・**提案概要:** best plan\n"
        "・**修正ガイドライン（NoGo時）:** guidance\n"
        "---"
    )


@pytest.mark.asyncio
async def test_execute_meeting_progresses_turns_and_finishes() -> None:
    decisions: list[PMDecision | Exception] = []
    for turn in range(1, 10):
        phase = "DIVERGENCE" if turn <= 4 else "CONFLICT" if turn <= 7 else "FINAL"
        target = "marketing" if turn % 2 == 1 else "dev"
        decisions.append(
            make_decision(
                speech=f"pm{turn}",
                phase=phase,
                next_action="CALL_AGENT",
                target_agent=target,
                instruction_for_target=f"ask {target}",
            )
        )
    decisions.append(
        make_decision(speech="pm final", phase="FINAL", next_action="FINISH_FOR_PRESIDENT")
    )
    pm = StubPM(decisions)
    marketing = StubAgent("marketing", "marketing reply")
    dev = StubAgent("dev", "dev reply")
    channel = StubChannel()

    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=pm,
        other_agents={"marketing": marketing, "dev": dev},
        president_mention="@president",
    )

    async def fake_post(*args: Any, **kwargs: Any) -> None:
        return None

    orchestrator._post_via_webhook = fake_post  # type: ignore[method-assign]

    final_pm_speech, history, final_decision = await orchestrator.execute_meeting("theme", channel)

    assert final_pm_speech.endswith("pm final")
    assert final_decision is not None
    assert final_decision.next_action == "FINISH_FOR_PRESIDENT"
    assert len(marketing.calls) == 4
    assert len(dev.calls) == 5
    assert len(history) == 19


@pytest.mark.asyncio
async def test_execute_meeting_reports_pm_failure() -> None:
    pm = StubPM([RuntimeError("pm crashed")])
    marketing = StubAgent("marketing", "marketing reply")
    dev = StubAgent("dev", "dev reply")
    channel = StubChannel()

    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=pm,
        other_agents={"marketing": marketing, "dev": dev},
        president_mention="@president",
    )

    async def fake_post(*args: Any, **kwargs: Any) -> None:
        return None

    orchestrator._post_via_webhook = fake_post  # type: ignore[method-assign]

    final_pm_speech, history, final_decision = await orchestrator.execute_meeting("theme", channel)

    assert final_pm_speech == ""
    assert history == []
    assert final_decision is None
    assert any("PMエージェント応答中にエラー" in message for message in channel.messages)


@pytest.mark.asyncio
async def test_execute_meeting_reports_pm_post_failure() -> None:
    decisions = [
        make_decision(
            speech="pm1",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="ask marketing",
        )
    ]
    pm = StubPM(decisions)
    marketing = StubAgent("marketing", "marketing reply")
    dev = StubAgent("dev", "dev reply")
    channel = StubChannel()

    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=pm,
        other_agents={"marketing": marketing, "dev": dev},
        president_mention="@president",
    )

    async def raise_post(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("post failed")

    orchestrator._post_via_webhook = raise_post  # type: ignore[method-assign]

    final_pm_speech, history, final_decision = await orchestrator.execute_meeting("theme", channel)

    assert final_pm_speech == ""
    assert history == []
    assert final_decision is None
    assert any("PMの投稿中にエラー" in message for message in channel.messages)


@pytest.mark.asyncio
async def test_execute_meeting_allows_early_finish_before_max_turn() -> None:
    decisions = [
        make_decision(
            speech="初回提案",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="案を出して",
        ),
        make_decision(
            speech="候補を比較したので合意できた、最終提案に進みます",
            phase="CONFLICT",
            next_action="FINISH_FOR_PRESIDENT",
        ),
        make_decision(
            speech="追加の比較観点を出します",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="案A/案Bの訴求を比較して",
        ),
        make_decision(
            speech="技術面の比較を続けます",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="実装コストのトレードオフを比較して",
        ),
        make_decision(
            speech="終盤に向けて優先順位を確認します",
            phase="CONFLICT",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="どちらを採用するか比較して",
        ),
        make_decision(
            speech="比較完了、最終提案に進みます",
            phase="CONFLICT",
            next_action="FINISH_FOR_PRESIDENT",
        ),
    ]

    pm = StubPM(decisions)
    marketing = StubAgent("marketing", "合意です。この案で行きましょう")
    dev = StubAgent("dev", "技術的にも成立します")
    channel = StubChannel()

    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=pm,
        other_agents={"marketing": marketing, "dev": dev},
        president_mention="@president",
    )

    async def fake_post(*args: Any, **kwargs: Any) -> None:
        return None

    orchestrator._post_via_webhook = fake_post  # type: ignore[method-assign]

    final_pm_speech, history, final_decision = await orchestrator.execute_meeting("theme", channel)

    assert final_decision is not None
    assert final_decision.next_action == "FINISH_FOR_PRESIDENT"
    assert final_pm_speech == "比較完了、最終提案に進みます"
    assert len(history) == 11
    assert not any("まだ最終ターンではないため、議論を継続します" in message for message in channel.messages)


def test_phase_drift_is_relaxed_when_convergence_keywords_exist() -> None:
    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=StubPM([]),
        other_agents={},
        president_mention="@president",
    )

    history = [
        "すずかちゃん(PM): 候補を2つに絞り、最終案を決めましょう",
        "ヂャイアン(マーケ): その方向で合意です",
    ]

    result = orchestrator._recent_log_suggests_convergence(
        history,
        "この案で決定します",
        "残す案の核を明確化して",
    )

    assert result is True


def test_phase_drift_is_not_relaxed_without_convergence_keywords() -> None:
    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=StubPM([]),
        other_agents={},
        president_mention="@president",
    )

    history = [
        "すずかちゃん(PM): もう1案追加して広げよう",
        "ヂャイアン(マーケ): さらに別ジャンルも試そう",
    ]

    result = orchestrator._recent_log_suggests_convergence(
        history,
        "新しい案を追加したいです",
        "さらに発散しましょう",
    )

    assert result is False


def test_tradeoff_comparison_detects_real_comparison_context() -> None:
    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=StubPM([]),
        other_agents={},
        president_mention="@president",
    )

    history = [
        "PM: 案Aと案Bを比較し、どちらを採用するかトレードオフを整理しよう",
    ]

    assert orchestrator._has_tradeoff_comparison(history) is True


def test_tradeoff_comparison_rejects_single_keyword_false_positive() -> None:
    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=StubPM([]),
        other_agents={},
        president_mention="@president",
    )

    history = [
        "PM: この案を採用して進めます",
    ]

    assert orchestrator._has_tradeoff_comparison(history) is False


def test_select_next_non_pm_role_starts_with_marketing_then_alternates() -> None:
    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=StubPM([]),
        other_agents={"marketing": StubAgent("marketing", ""), "dev": StubAgent("dev", "")},
        president_mention="@president",
    )

    assert orchestrator._select_next_non_pm_role([], {"marketing": 0, "dev": 0}) == "marketing"

    history = ["PM: first", "ヂャイアン(マーケ): reply"]
    assert orchestrator._select_next_non_pm_role(history, {"marketing": 1, "dev": 0}) == "dev"

    history.append("PM: second")
    history.append("スゴ杉くん(エンジニア): reply")
    assert orchestrator._select_next_non_pm_role(history, {"marketing": 1, "dev": 1}) == "marketing"


@pytest.mark.asyncio
async def test_execute_meeting_overrides_pm_target_to_rotation_order() -> None:
    decisions = [
        make_decision(
            speech="pm1",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="ask dev first",
        ),
        make_decision(
            speech="pm2",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="ask marketing second",
        ),
        make_decision(
            speech="pm3",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="ask marketing again",
        ),
        make_decision(
            speech="pm final",
            phase="FINAL",
            next_action="FINISH_FOR_PRESIDENT",
        ),
    ]
    pm = StubPM(decisions)
    marketing = StubAgent("marketing", "marketing reply")
    dev = StubAgent("dev", "dev reply")
    channel = StubChannel()

    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=pm,
        other_agents={"marketing": marketing, "dev": dev},
        president_mention="@president",
    )

    async def fake_post(*args: Any, **kwargs: Any) -> None:
        return None

    orchestrator._post_via_webhook = fake_post  # type: ignore[method-assign]

    await orchestrator.execute_meeting("theme", channel)

    assert len(marketing.calls) == 2
    assert len(dev.calls) == 2
    assert dev.calls[0][0].startswith("ask dev first")
    assert marketing.calls[0][0].startswith("ask marketing second")
    assert orchestrator.last_meeting_trace[0]["target_final"] == "dev"
    assert "force_dev_in_divergence" in orchestrator.last_meeting_trace[0]["guardrails"]
    assert orchestrator.last_meeting_trace[1]["target_final"] == "marketing"
    assert orchestrator.last_meeting_trace[2]["target_final"] == "dev"
    assert "rotation_adjusted" in orchestrator.last_meeting_trace[2]["guardrails"]


@pytest.mark.asyncio
async def test_dev_agent_second_turn_prompt_blocks_greeting_and_president_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = Path(__file__).resolve().parents[1] / "src" / "agents" / "dev" / "config.yaml"
    agent = DevAgent(api_key="dummy", config_path=str(config_path), mention_id="@スゴ杉くん(エンジニア)")
    captured: dict[str, Any] = {}

    async def fake_generate_content_with_retry(**kwargs: Any) -> Any:
        captured.update(kwargs)

        class Response:
            text = "ok"

        return Response()

    monkeypatch.setattr(agent, "generate_content_with_retry", fake_generate_content_with_retry)

    history = [
        "すずかちゃん(PM): まずは方針を整理します",
        "スゴ杉くん(エンジニア): 初回の技術メモです",
    ]
    reply = await agent.think_and_reply("技術面の比較を続けて", history)

    assert reply == "ok"
    system_instruction = captured["config"].system_instruction
    assert "2回目以降は挨拶、締めの定型句、社長確認の定型句を禁止" in system_instruction
    assert "社長への確認・呼びかけ" in system_instruction
    assert "ヂャイアンの発言履歴あり=False" in system_instruction


@pytest.mark.asyncio
async def test_execute_meeting_does_not_inject_deadline_reminder_prefix() -> None:
    decisions = [
        make_decision(
            speech="この案を比較していきます",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="技術観点を整理して",
        ),
        make_decision(
            speech="マーケ観点も比較します",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="案A/案Bの拡散性を比較して",
        ),
        make_decision(
            speech="トレードオフを整理します",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="実装負荷のトレードオフを比較して",
        ),
        make_decision(
            speech="採用/不採用を詰めます",
            phase="CONFLICT",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="どちらを採用するか比較して",
        ),
        make_decision(
            speech="最終確認",
            phase="CONFLICT",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="懸念点を最終確認して",
        ),
        make_decision(
            speech="この案で決定します",
            phase="CONFLICT",
            next_action="FINISH_FOR_PRESIDENT",
        ),
    ]

    pm = StubPM(decisions)
    marketing = StubAgent("marketing", "拡散性は高いです")
    dev = StubAgent("dev", "実装可能です")
    channel = StubChannel()

    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=pm,
        other_agents={"marketing": marketing, "dev": dev},
        president_mention="@president",
    )

    async def fake_post(*args: Any, **kwargs: Any) -> None:
        return None

    orchestrator._post_via_webhook = fake_post  # type: ignore[method-assign]

    final_pm_speech, history, final_decision = await orchestrator.execute_meeting("theme", channel)

    assert final_decision is not None
    assert final_pm_speech == "この案で決定します"
    assert all("議論できるのはあと" not in line for line in history)


@pytest.mark.asyncio
async def test_execute_meeting_skips_phase_drift_warning_when_converging() -> None:
    decisions = [
        make_decision(
            speech="候補を2つに絞って、この案で決定しましょう",
            phase="CONFLICT",
            next_action="FINISH_FOR_PRESIDENT",
        ),
        make_decision(
            speech="比較軸を明確化します",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="案A/案Bの実装差分を比較して",
        ),
        make_decision(
            speech="マーケ軸も確認します",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="拡散導線を比較して",
        ),
        make_decision(
            speech="優先順位を整理します",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="トレードオフを比較して",
        ),
        make_decision(
            speech="採用案を詰めます",
            phase="CONFLICT",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="どちらを採用するか比較して",
        ),
        make_decision(
            speech="最終的にこの案で決定",
            phase="CONFLICT",
            next_action="FINISH_FOR_PRESIDENT",
        ),
    ]

    pm = StubPM(decisions)
    marketing = StubAgent("marketing", "")
    dev = StubAgent("dev", "")
    channel = StubChannel()

    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=pm,
        other_agents={"marketing": marketing, "dev": dev},
        president_mention="@president",
    )

    async def fake_post(*args: Any, **kwargs: Any) -> None:
        return None

    orchestrator._post_via_webhook = fake_post  # type: ignore[method-assign]

    final_pm_speech, history, final_decision = await orchestrator.execute_meeting("theme", channel)

    assert final_decision is not None
    assert final_pm_speech == "最終的にこの案で決定"
    assert len(history) == 11
    assert not any("進行フェーズがターン目標から逸脱しています" in message for message in channel.messages)

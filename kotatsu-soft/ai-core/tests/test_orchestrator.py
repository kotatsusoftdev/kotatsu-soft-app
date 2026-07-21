from typing import Any

import pytest

from agents.pm.schemas import PMDecision
from orchestrator import DynamicOrchestrator


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


@pytest.mark.asyncio
async def test_execute_meeting_progresses_turns_and_finishes() -> None:
    decisions: list[PMDecision | Exception] = []
    for turn in range(1, 10):
        phase = "DIVERGENCE" if turn <= 4 else "CONFLICT" if turn <= 8 else "FINAL"
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
    assert len(marketing.calls) == 5
    assert len(dev.calls) == 4
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

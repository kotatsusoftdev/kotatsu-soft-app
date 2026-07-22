from __future__ import annotations

import asyncio

from agents.pm.schemas import PMDecision
from main import _append_meeting_turn_audit_record
from orchestrator import DynamicOrchestrator


class StubPM:
    name = "PM"
    avatar_url = "https://example.com/pm.png"

    def __init__(self, decisions: list[PMDecision]):
        self._decisions = list(decisions)

    async def decide_next_step(self, *args, **kwargs) -> PMDecision:
        if not self._decisions:
            raise RuntimeError("No stub decisions left")
        return self._decisions.pop(0)


class StubAgent:
    def __init__(self, name: str, reply: str):
        self.name = name
        self.avatar_url = "https://example.com/agent.png"
        self.reply = reply

    async def think_and_reply(self, prompt: str, conversation_history: list[str]) -> str:
        return self.reply


class StubChannel:
    def __init__(self):
        self.id = 999999
        self.messages: list[str] = []

    async def send(self, content: str, **kwargs) -> None:
        self.messages.append(content)


async def fake_post(*args, **kwargs) -> None:
    return None


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
        final_recommendation="sample plan",
        final_category="sample",
        revision_guidance="sample guidance",
    )


async def main() -> None:
    decisions = [
        make_decision(
            speech="まずは案を広げよう",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="別案を1つ追加して",
        ),
        make_decision(
            speech="案Aと案Bの実装面を見たい",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="案A/案Bの実装コストを比較して",
        ),
        make_decision(
            speech="集客面でも案A/案Bを比べよう",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="拡散性を比較して",
        ),
        make_decision(
            speech="発散の最後に技術ギミック案を補強",
            phase="DIVERGENCE",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="技術ギミックの別案を追加して",
        ),
        make_decision(
            speech="ここから候補を比較検討",
            phase="CONFLICT",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="採用理由/不採用理由を比較して",
        ),
        make_decision(
            speech="トレードオフを整理",
            phase="CONFLICT",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="実装負荷と訴求のトレードオフを比較して",
        ),
        make_decision(
            speech="優先順位を決める",
            phase="CONFLICT",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="どちらを優先するか明確にして",
        ),
        make_decision(
            speech="最終候補1案に絞る",
            phase="FINAL",
            next_action="CALL_AGENT",
            target_agent="dev",
            instruction_for_target="最終案の実装リスク確認",
        ),
        make_decision(
            speech="提出前の最終確認",
            phase="FINAL",
            next_action="CALL_AGENT",
            target_agent="marketing",
            instruction_for_target="最終案の訴求整理",
        ),
        make_decision(
            speech="最終提案を提出します",
            phase="FINAL",
            next_action="FINISH_FOR_PRESIDENT",
        ),
    ]

    pm = StubPM(decisions)
    marketing = StubAgent("ヂャイアン(マーケ)", "マーケ回答")
    dev = StubAgent("スゴ杉くん(エンジニア)", "開発回答")
    channel = StubChannel()

    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=pm,
        other_agents={"marketing": marketing, "dev": dev},
        president_mention="@president",
    )
    orchestrator._post_via_webhook = fake_post

    _final_pm_speech, history, final_decision = await orchestrator.execute_meeting("sample theme", channel)

    _append_meeting_turn_audit_record(
        channel_id=channel.id,
        theme="sample theme",
        revision_guidance=None,
        trace=orchestrator.last_meeting_trace,
        history=history,
        final_decision=final_decision,
    )


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
from pathlib import Path
import re
from typing import Awaitable, Callable, Optional

import aiohttp
import discord
from agents.base_agent import BaseAgent
from agents.pm.agent import PMAgent
from agents.pm.schemas import PMDecision


class DynamicOrchestrator:
    WEBHOOK_NAME = "Kotatsu AI 会話"
    _DEADLINE_REMINDER_RE = re.compile(
        r"^\s*(?:[-・*]\s*)?議論できるのはあと\s*\d+\s*回よ。そろそろ絞り込みましょう\s*\n*"
    )

    def __init__(
        self,
        webhook_url: Optional[str],
        pm_agent: PMAgent,
        other_agents: dict[str, BaseAgent],
        president_mention: str,
    ):
        self.webhook_url = webhook_url
        self.pm = pm_agent
        self.other_agents = other_agents
        self.president_mention = president_mention
        self.channel_webhooks: dict[int, discord.Webhook] = {}

    @staticmethod
    def _expected_phase_for_turn(current_turn: int) -> str:
        if current_turn <= 4:
            return "DIVERGENCE"
        if current_turn <= 8:
            return "CONFLICT"
        return "FINAL"

    @staticmethod
    def _deadline_prefix(current_turn: int, max_turns: int) -> str:
        remaining = max(max_turns - current_turn + 1, 0)
        return f"議論できるのはあと{remaining}回よ。そろそろ絞り込みましょう"

    @classmethod
    def _strip_leading_deadline_reminders(cls, text: Optional[str]) -> str:
        cleaned = (text or "").lstrip()
        while True:
            match = cls._DEADLINE_REMINDER_RE.match(cleaned)
            if not match:
                break
            cleaned = cleaned[match.end():].lstrip()
        return cleaned

    def _ensure_deadline_prefix(self, text: Optional[str], current_turn: int, max_turns: int) -> str:
        body = self._strip_leading_deadline_reminders(text)
        if current_turn < 5:
            return body

        prefix = self._deadline_prefix(current_turn, max_turns)
        if not body:
            return prefix
        return f"{prefix}\n{body}"

    @staticmethod
    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in text for keyword in keywords)

    def _looks_like_expansion(self, text: str) -> bool:
        expansion_words = (
            "新しい案",
            "追加",
            "さらに案",
            "新機能",
            "別ジャンル",
            "もう1案",
            "アイデアを増",
            "広げ",
        )
        return self._contains_any(text, expansion_words)

    def _looks_like_narrowing(self, text: str) -> bool:
        narrowing_words = (
            "削",
            "捨",
            "絞",
            "二者択一",
            "どちら",
            "トレードオフ",
            "優先",
        )
        return self._contains_any(text, narrowing_words)

    @staticmethod
    def _split_discord_message(text: str, limit: int = 2000) -> list[str]:
        if not text:
            return [""]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break

            split_at = remaining.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit

            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]

        return chunks

    async def _get_channel_webhook(
        self,
        channel: discord.TextChannel,
    ) -> Optional[discord.Webhook]:
        cached_webhook = self.channel_webhooks.get(channel.id)
        if cached_webhook:
            return cached_webhook

        try:
            existing_webhooks = await channel.webhooks()
            reusable = next(
                (wh for wh in existing_webhooks if wh.name == self.WEBHOOK_NAME),
                None,
            )
            if reusable:
                self.channel_webhooks[channel.id] = reusable
                return reusable
        except (discord.Forbidden, discord.HTTPException):
            # Listing webhooks may fail when permissions are missing.
            pass

        try:
            created = await channel.create_webhook(
                name=self.WEBHOOK_NAME,
                reason="Use webhook to render distinct AI agent voices in Discord",
            )
            self.channel_webhooks[channel.id] = created
            return created
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _post_via_webhook(
        self,
        agent: BaseAgent,
        text: str,
        channel: discord.TextChannel,
    ) -> None:
        chunks = self._split_discord_message(text)

        async with channel.typing():
            typing_delay = min(max(len(text) // 25, 2), 6)
            await asyncio.sleep(typing_delay)

        if self.webhook_url:
            try:
                async with aiohttp.ClientSession() as session:
                    webhook = discord.Webhook.from_url(self.webhook_url, session=session)
                    for chunk in chunks:
                        await webhook.send(
                            content=chunk,
                            username=agent.name,
                            avatar_url=agent.avatar_url,
                        )
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                print(f"[orchestrator] Webhook URL send failed, fallback to channel message: {exc}")

        webhook = await self._get_channel_webhook(channel)
        if webhook and webhook.token:
            try:
                async with aiohttp.ClientSession() as session:
                    dynamic_webhook = discord.Webhook.from_url(webhook.url, session=session)
                    for chunk in chunks:
                        await dynamic_webhook.send(
                            content=chunk,
                            username=agent.name,
                            avatar_url=agent.avatar_url,
                        )
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                self.channel_webhooks.pop(channel.id, None)
                print(f"[orchestrator] Channel webhook send failed, fallback to channel message: {exc}")
        elif webhook and not webhook.token:
            # Some retrieved webhooks may not expose a token; fallback safely.
            self.channel_webhooks.pop(channel.id, None)

        for chunk in chunks:
            await channel.send(chunk)

    def _identify_consulted_agents(self, history: list[str]) -> set[str]:
        consulted = set()
        for line in history:
            if (
                "ヂャイアン(マーケ)" in line
                or "@ヂャイアン(マーケ)" in line
                or "Gian_Agent" in line
                or "@Gian_Agent" in line
            ):
                consulted.add("marketing")
            if (
                "スゴ杉くん(エンジニア)" in line
                or "@スゴ杉くん(エンジニア)" in line
                or "Takasugi_Agent" in line
                or "@Takasugi_Agent" in line
            ):
                consulted.add("dev")
        return consulted

    async def execute_meeting(
        self,
        theme: str,
        channel: discord.TextChannel,
        revision_guidance: Optional[str] = None,
    ) -> tuple[str, list[str], Optional[PMDecision]]:
        history: list[str] = []
        max_turns = 10
        current_turn = 1
        last_pm_speech = ""
        final_decision: Optional[PMDecision] = None
        consulted_counts: dict[str, int] = {"marketing": 0, "dev": 0}
        per_role_min = 2
        force_convergence_next_turn = False

        while current_turn <= max_turns:
            try:
                decision = await self.pm.decide_next_step(
                    theme,
                    history,
                    current_turn,
                    max_turns,
                    revision_guidance=revision_guidance,
                    force_convergence=force_convergence_next_turn,
                )
                force_convergence_next_turn = False
            except Exception as exc:
                error_text = str(exc)
                if len(error_text) > 1200:
                    error_text = error_text[:1200] + "...（省略）"
                content = f"⚠️ PMエージェント応答中にエラーが発生しました: {error_text}"
                try:
                    await channel.send(content)
                except Exception as send_exc:
                    print(f"[orchestrator] Failed to report PM error in channel: {send_exc}")
                break

            # hard guardrail: turn-based phase goal validation and convergence escalation
            expected_phase = self._expected_phase_for_turn(current_turn)
            phase_drift = decision.phase != expected_phase
            if phase_drift:
                await channel.send(
                    "⚠️ 進行フェーズがターン目標から逸脱しています。"
                    f" Turn {current_turn} は {expected_phase} フェーズとして強制収束モードへ移行します。"
                )
                force_convergence_next_turn = True
                decision.phase = expected_phase

            if current_turn >= 5:
                decision.speech = self._ensure_deadline_prefix(
                    decision.speech,
                    current_turn,
                    max_turns,
                )
                if decision.next_action == "CALL_AGENT":
                    decision.instruction_for_target = self._ensure_deadline_prefix(
                        decision.instruction_for_target,
                        current_turn,
                        max_turns,
                    )

            if 5 <= current_turn <= 8 and decision.next_action == "CALL_AGENT":
                current_instruction = (decision.instruction_for_target or "").strip()
                if not self._looks_like_narrowing(current_instruction):
                    force_convergence_next_turn = True
                    decision.instruction_for_target = (
                        f"{self._deadline_prefix(current_turn, max_turns)}\n"
                        "ここからは衝突・削ぎ落としフェーズです。"
                        "残す要素と捨てる要素を明示し、二者択一でどちらを削るかを決めてください。"
                        "新規アイデアの追加は禁止し、1日実装可能性と面白さのトレードオフを必ず示してください。"
                    )

            if current_turn in (8, 9):
                inspection_text = (decision.speech or "") + "\n" + (decision.instruction_for_target or "")
                if self._looks_like_expansion(inspection_text):
                    force_convergence_next_turn = True
                    if decision.next_action == "CALL_AGENT":
                        decision.instruction_for_target = (
                            f"{self._deadline_prefix(current_turn, max_turns)}\n"
                            "終盤のため新しい大風呂敷は禁止。これまでの候補から削る対象を2つ挙げ、"
                            "どちらを捨てるかを今ターンで決め、最終的に残す1案の核だけを返答してください。"
                        )

            try:
                await self._post_via_webhook(self.pm, decision.speech, channel)
            except Exception as exc:
                await channel.send(
                    f"⚠️ PMの投稿中にエラーが発生しました: {exc}"
                )
                break

            history.append(f"{self.pm.name}: {decision.speech}")
            last_pm_speech = decision.speech
            # enforce multi-exchange: require each expert to be consulted at least `per_role_min` times
            if current_turn < max_turns and decision.next_action == "FINISH_FOR_PRESIDENT":
                await channel.send(
                    "⚠️ まだ最終ターンではないため、議論を継続します。"
                    " 未確認の観点をさらに深掘りして、次の専門家に具体的な問いを投げてください。"
                )
                decision.next_action = "CALL_AGENT"
                if not decision.target_agent or decision.target_agent not in self.other_agents:
                    decision.target_agent = (
                        "marketing"
                        if consulted_counts.get("marketing", 0) <= consulted_counts.get("dev", 0)
                        else "dev"
                    )
                if not decision.instruction_for_target or not decision.instruction_for_target.strip():
                    decision.instruction_for_target = (
                        "PMからの依頼です。まだ議論が続いている段階です。"
                        " 次の専門家として、このテーマに対する追加の観点や改善案を2〜3点挙げ、"
                        "前の発言を踏まえて新しい視点を提供してください。"
                    )

            if decision.next_action == "CALL_AGENT":
                if not decision.target_agent or decision.target_agent not in self.other_agents:
                    decision.target_agent = (
                        "marketing"
                        if consulted_counts.get("marketing", 0) <= consulted_counts.get("dev", 0)
                        else "dev"
                    )
                if not decision.instruction_for_target or not decision.instruction_for_target.strip():
                    decision.instruction_for_target = (
                        "PMからの依頼です。まだ議論が続いている段階です。"
                        " 次の専門家として、このテーマに対する追加の観点や改善案を2〜3点挙げ、"
                        "前の発言を踏まえて新しい視点を提供してください。"
                    )

            if decision.next_action == "FINISH_FOR_PRESIDENT":
                if (
                    consulted_counts.get("marketing", 0) < per_role_min
                    or consulted_counts.get("dev", 0) < per_role_min
                ) and current_turn < max_turns:
                    await channel.send(
                        "⚠️ PMが終了を提案しましたが、まだ十分な専門意見が集まっていません。"
                        " 未相談のエージェントへ再度問いかけます。"
                    )
                    target = (
                        "marketing"
                        if consulted_counts.get("marketing", 0) <= consulted_counts.get("dev", 0)
                        else "dev"
                    )
                    decision.next_action = "CALL_AGENT"
                    decision.target_agent = target
                    decision.instruction_for_target = (
                        "PMからの依頼です。まだ議論に参加していない、または意見が不足している専門家として、"
                        "このテーマに対する追加の観点や改善案を2〜3点挙げてください。"
                    )
                else:
                    final_decision = decision
                    break

            if current_turn >= max_turns and decision.next_action != "FINISH_FOR_PRESIDENT":
                await channel.send(
                    "⚠️ 最終ターンに到達したため、議論を集約して最終案をまとめます。"
                )
                decision.next_action = "FINISH_FOR_PRESIDENT"

            if decision.next_action != "CALL_AGENT":
                if decision.next_action == "FINISH_FOR_PRESIDENT":
                    final_decision = decision
                    break
                await channel.send(
                    "⚠️ PMの指示が不明瞭でした。議論を継続し、再度エージェントの意見を収集します。"
                )
                current_turn += 1
                continue

            target_role = decision.target_agent
            target_agent = self.other_agents.get(target_role)
            if target_agent is None:
                await channel.send(
                    "⚠️ 次の担当エージェントが指定されていません。PMに戻して進行を継続します。"
                )
                current_turn += 1
                continue

            instruction = (
                decision.instruction_for_target
                or "提示された内容について専門観点から分析・回答してください。"
            )
            if revision_guidance:
                instruction = (
                    "【社長のNoGo修正方針（最優先）】\n"
                    f"{revision_guidance}\n\n"
                    f"{instruction}\n\n"
                    "上記の修正方針を満たす前提で回答してください。"
                )

            try:
                reply_text = await target_agent.think_and_reply(instruction, history)
            except Exception as exc:
                await channel.send(
                    f"⚠️ {target_agent.name} の応答中にエラーが発生しました: {exc}"
                )
                current_turn += 1
                continue

            try:
                await self._post_via_webhook(target_agent, reply_text, channel)
            except Exception as exc:
                await channel.send(
                    f"⚠️ {target_agent.name} の投稿中にエラーが発生しました: {exc}"
                )
                break

            history.append(f"{target_agent.name}: {reply_text}")
            # track how many times each agent has been consulted
            if target_role in consulted_counts:
                consulted_counts[target_role] += 1
            else:
                consulted_counts[target_role] = 1
            current_turn += 1

        return last_pm_speech, history, final_decision


class ProposalSelectView(discord.ui.View):
    def __init__(
        self,
        final_recommendation: str,
        final_category: Optional[str],
        revision_guidance: Optional[str],
        pm_agent: PMAgent,
        meeting_channel: discord.TextChannel,
        theme: str,
        rerun_meeting: Optional[Callable[[str, discord.TextChannel, Optional[str]], Awaitable[None]]] = None,
    ):
        super().__init__(timeout=None)
        self.final_recommendation = final_recommendation
        self.final_category = final_category or "未定"
        self.revision_guidance = revision_guidance or "改善の方向性を明確にして、再度検討してください。"
        self.pm_agent = pm_agent
        self.meeting_channel = meeting_channel
        self.theme = theme
        self.rerun_meeting = rerun_meeting

        go_button = discord.ui.Button(
            label="Go",
            style=discord.ButtonStyle.success,
            custom_id="president_go",
        )
        go_button.callback = self._make_callback("go")
        self.add_item(go_button)

        no_go_button = discord.ui.Button(
            label="NoGo",
            style=discord.ButtonStyle.danger,
            custom_id="president_nogo",
        )
        no_go_button.callback = self._make_callback("nogo")
        self.add_item(no_go_button)

    def _disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    def _make_callback(self, decision: str):
        async def callback(interaction: discord.Interaction):
            await self._finalize(interaction, decision)

        return callback

    async def _finalize(self, interaction: discord.Interaction, decision: str) -> None:
        if decision == "go":
            plan_label = self.final_category
            self._disable_buttons()
            await interaction.message.edit(view=self)
            await interaction.response.send_message(
                f"Go判定: **{plan_label}**。仕様書を自動出力中...",
                ephemeral=False,
            )

            spec_path = await self.pm_agent.generate_spec_for_plan(
                selected_plan=plan_label,
                proposal_summary=self.final_recommendation,
            )
            await interaction.followup.send(
                f"📄 仕様書を出力・保存しました: `{spec_path}`"
            )
            return

        await interaction.response.send_modal(NoGoRevisionModal(self))


class NoGoRevisionModal(discord.ui.Modal, title="NoGo時の修正方針"):
    def __init__(self, parent_view: ProposalSelectView):
        super().__init__()
        self.parent_view = parent_view
        self.revision_input = discord.ui.TextInput(
            label="修正方針",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            default=parent_view.revision_guidance,
            placeholder="例: 対象ユーザーを小学生に限定し、1プレイ30秒以内にしてください",
        )
        self.add_item(self.revision_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guidance = self.revision_input.value.strip() or self.parent_view.revision_guidance
        self.parent_view._disable_buttons()
        if interaction.message:
            await interaction.message.edit(view=self.parent_view)

        await interaction.response.send_message(
            "NoGoを受け付けました。修正方針を反映して再検討を開始します。",
            ephemeral=False,
        )

        if self.parent_view.rerun_meeting:
            await self.parent_view.rerun_meeting(
                self.parent_view.theme,
                self.parent_view.meeting_channel,
                guidance,
            )

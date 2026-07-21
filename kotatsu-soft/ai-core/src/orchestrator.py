import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional

import aiohttp
import discord
from agents.base_agent import BaseAgent
from agents.pm.agent import PMAgent
from agents.pm.schemas import PMDecision


class DynamicOrchestrator:
    WEBHOOK_NAME = "Kotatsu AI 会話"

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
                "ジャイアン(マーケ)" in line
                or "@ジャイアン(マーケ)" in line
                or "Gian_Agent" in line
                or "@Gian_Agent" in line
            ):
                consulted.add("marketing")
            if (
                "タカ杉くん(エンジニア)" in line
                or "@タカ杉くん(エンジニア)" in line
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

        while current_turn <= max_turns:
            try:
                decision = await self.pm.decide_next_step(
                    theme,
                    history,
                    current_turn,
                    max_turns,
                    revision_guidance=revision_guidance,
                )
            except Exception as exc:
                error_text = str(exc)
                if len(error_text) > 1200:
                    error_text = error_text[:1200] + "...（省略）"
                content = f"⚠️ PMエージェント応答中にエラーが発生しました: {error_text}"
                try:
                    await channel.send(content)
                except Exception:
                    await channel.send(
                        "⚠️ PMエージェント応答中にエラーが発生しました。詳細はログを確認してください。"
                    )
                break

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

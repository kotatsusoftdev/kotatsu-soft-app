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
    _NON_PM_AGENT_ORDER = ("marketing", "dev")
    _CONVERGENCE_KEYWORDS = (
        "絞",
        "残す",
        "捨て",
        "削",
        "決定",
        "決め",
        "合意",
        "一本化",
        "収束",
        "採用",
        "最終",
        "確定",
    )
    _COMPARISON_STRONG_KEYWORDS = (
        "比較",
        "トレードオフ",
        "二者択一",
        "案A",
        "案B",
        "メリット",
        "デメリット",
        "採用理由",
        "不採用理由",
        "優先順位",
    )
    _COMPARISON_DECISION_KEYWORDS = (
        "どちら",
        "優先",
        "残す",
        "捨て",
        "削る",
        "諦め",
        "採用",
        "不採用",
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
        self.last_meeting_trace: list[dict] = []

    @staticmethod
    def _expected_phase_for_turn(current_turn: int) -> str:
        if current_turn <= 4:
            return "DIVERGENCE"
        if current_turn <= 7:
            return "CONFLICT"
        return "FINAL"

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

    def _has_tradeoff_comparison(
        self,
        history: list[str],
        decision: Optional[PMDecision] = None,
    ) -> bool:
        parts = ["\n".join(history[-10:])]
        if decision:
            parts.append(decision.speech or "")
            parts.append(decision.instruction_for_target or "")
        text = "\n".join(parts)
        has_strong = self._contains_any(text, self._COMPARISON_STRONG_KEYWORDS)
        has_decision = self._contains_any(text, self._COMPARISON_DECISION_KEYWORDS)
        has_explicit_two_options = "案A" in text and "案B" in text
        return has_explicit_two_options or (has_strong and has_decision)

    @staticmethod
    def _normalize_for_repeat_check(text: str) -> str:
        normalized = re.sub(r"\s+", "", text or "")
        normalized = re.sub(r"[、。,.!！?？:：\-・]", "", normalized)
        return normalized

    def _recent_log_suggests_convergence(
        self,
        history: list[str],
        decision_speech: Optional[str],
        instruction_for_target: Optional[str],
        *,
        window_size: int = 6,
    ) -> bool:
        recent_lines = history[-window_size:] if window_size > 0 else history
        recent_text = "\n".join(recent_lines)
        inspection_text = "\n".join(
            [recent_text, decision_speech or "", instruction_for_target or ""]
        )
        return self._contains_any(inspection_text, self._CONVERGENCE_KEYWORDS)

    def _is_repetitive_pm_speech(self, previous_pm_speech: str, current_pm_speech: str) -> bool:
        if not previous_pm_speech or not current_pm_speech:
            return False

        previous_core = self._normalize_for_repeat_check(previous_pm_speech)
        current_core = self._normalize_for_repeat_check(current_pm_speech)
        return bool(previous_core) and previous_core == current_core

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

    @staticmethod
    def _history_line_mentions_role(line: str, role: str) -> bool:
        role_markers = {
            "marketing": (
                "ヂャイアン(マーケ):",
                "@ヂャイアン(マーケ)",
                "Gian_Agent:",
                "@Gian_Agent",
                "marketing:",
            ),
            "dev": (
                "スゴ杉くん(エンジニア):",
                "@スゴ杉くん(エンジニア)",
                "Takasugi_Agent:",
                "@Takasugi_Agent",
                "dev:",
            ),
        }
        return any(marker in line for marker in role_markers.get(role, ()))

    def _last_non_pm_role(self, history: list[str]) -> Optional[str]:
        for line in reversed(history):
            for role in self._NON_PM_AGENT_ORDER:
                if self._history_line_mentions_role(line, role):
                    return role
        return None

    def _select_next_non_pm_role(
        self,
        history: list[str],
        consulted_counts: dict[str, int],
    ) -> Optional[str]:
        available_roles = [role for role in self._NON_PM_AGENT_ORDER if role in self.other_agents]
        if not available_roles:
            return None

        for role in available_roles:
            if consulted_counts.get(role, 0) == 0:
                return role

        last_role = self._last_non_pm_role(history)
        if last_role in available_roles and len(available_roles) > 1:
            last_index = available_roles.index(last_role)
            return available_roles[(last_index + 1) % len(available_roles)]

        return min(available_roles, key=lambda role: consulted_counts.get(role, 0))

    async def execute_meeting(
        self,
        theme: str,
        channel: discord.TextChannel,
        revision_guidance: Optional[str] = None,
    ) -> tuple[str, list[str], Optional[PMDecision]]:
        self.last_meeting_trace = []
        history: list[str] = []
        max_turns = 10
        current_turn = 1
        last_pm_speech = ""
        final_decision: Optional[PMDecision] = None
        consulted_counts: dict[str, int] = {"marketing": 0, "dev": 0}
        force_convergence_next_turn = False

        while current_turn <= max_turns:
            was_forced_convergence = force_convergence_next_turn
            try:
                decision = await self.pm.decide_next_step(
                    theme,
                    history,
                    current_turn,
                    max_turns,
                    revision_guidance=revision_guidance,
                    force_convergence=was_forced_convergence,
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
            convergence_in_context = self._recent_log_suggests_convergence(
                history,
                decision.speech,
                decision.instruction_for_target,
            )
            trace_entry = {
                "turn": current_turn,
                "expected_phase": expected_phase,
                "pm_phase_initial": decision.phase,
                "next_action_initial": decision.next_action,
                "target_initial": decision.target_agent,
                "consulted_before": dict(consulted_counts),
                "convergence_keywords_detected": convergence_in_context,
                "guardrails": [],
            }
            phase_drift = decision.phase != expected_phase and not convergence_in_context
            if phase_drift:
                await channel.send(
                    "⚠️ 進行フェーズがターン目標から逸脱しています。"
                    f" Turn {current_turn} は {expected_phase} フェーズとして強制収束モードへ移行します。"
                )
                force_convergence_next_turn = True
                decision.phase = expected_phase
                trace_entry["guardrails"].append("phase_drift_adjusted")

            if self._is_repetitive_pm_speech(last_pm_speech, decision.speech):
                force_convergence_next_turn = True
                trace_entry["guardrails"].append("repetitive_pm_speech")

            if 5 <= current_turn <= 7 and decision.next_action == "CALL_AGENT":
                current_instruction = (decision.instruction_for_target or "").strip()
                if not self._looks_like_narrowing(current_instruction):
                    force_convergence_next_turn = True
                    decision.instruction_for_target = (
                        "ここからは衝突・削ぎ落としフェーズです。"
                        "世界観の尖りを保ったまま、図形・文字・軽量アニメだけで最もシュールに見せる折衷案を最低1つ提示してください。"
                        "そのうえで何を優先し、何を諦めるかを自然な言葉で示してください。"
                    )
                    trace_entry["guardrails"].append("conflict_requires_narrowing")

            if current_turn in (8, 9):
                inspection_text = (decision.speech or "") + "\n" + (decision.instruction_for_target or "")
                if self._looks_like_expansion(inspection_text):
                    force_convergence_next_turn = True
                    if decision.next_action == "CALL_AGENT":
                        decision.instruction_for_target = (
                            "終盤のため新しい大風呂敷は禁止。これまでの候補から削る対象を2つ挙げ、"
                            "どちらを諦めるかを今ターンで決め、最終的に残す1案の核だけを返答してください。"
                        )
                    trace_entry["guardrails"].append("endgame_blocks_expansion")

            try:
                await self._post_via_webhook(self.pm, decision.speech, channel)
            except Exception as exc:
                await channel.send(
                    f"⚠️ PMの投稿中にエラーが発生しました: {exc}"
                )
                break

            history.append(f"{self.pm.name}: {decision.speech}")
            last_pm_speech = decision.speech

            if decision.next_action == "CALL_AGENT":
                expected_target = self._select_next_non_pm_role(history, consulted_counts)
                if expected_target and decision.target_agent != expected_target:
                    decision.target_agent = expected_target
                    trace_entry["guardrails"].append("rotation_adjusted")
                if not decision.target_agent or decision.target_agent not in self.other_agents:
                    decision.target_agent = expected_target
                if not decision.instruction_for_target or not decision.instruction_for_target.strip():
                    decision.instruction_for_target = (
                        "PMからの依頼です。まだ議論が続いている段階です。"
                        " 次の専門家として、このテーマに対する追加の観点や改善案を2〜3点挙げ、"
                        "前の発言を踏まえて新しい視点を提供してください。"
                    )

            # hard guardrail: Turn 1-5 never allow finish submission.
            if decision.next_action == "FINISH_FOR_PRESIDENT" and current_turn <= 5:
                decision.next_action = "CALL_AGENT"
                trace_entry["guardrails"].append("block_finish_before_turn6")
                if consulted_counts.get("dev", 0) == 0:
                    decision.target_agent = "dev"
                    decision.instruction_for_target = (
                        "まだ序盤フェーズです。提出は禁止。"
                        "エンジニア視点で実装コスト・技術アイデア・難所を具体化し、"
                        "既存案と異なる切り口の代替案も1つ示してください。"
                    )
                else:
                    decision.target_agent = "marketing"
                    decision.instruction_for_target = (
                        "まだ序盤フェーズです。提出は禁止。"
                        "既存案と異なる訴求軸の代替案を1つ追加し、"
                        "拡散導線の違いを簡潔に比較してください。"
                    )

            # hard guardrail: early finish allowed only after both roles and tradeoff comparison.
            if decision.next_action == "FINISH_FOR_PRESIDENT" and current_turn >= 6:
                has_both_perspectives = (
                    consulted_counts.get("marketing", 0) > 0
                    and consulted_counts.get("dev", 0) > 0
                )
                compared_tradeoffs = self._has_tradeoff_comparison(history, decision)
                trace_entry["finish_gate"] = {
                    "has_both_perspectives": has_both_perspectives,
                    "compared_tradeoffs": compared_tradeoffs,
                }
                if not (has_both_perspectives and compared_tradeoffs):
                    decision.next_action = "CALL_AGENT"
                    trace_entry["guardrails"].append("block_finish_until_both_roles_and_tradeoff")
                    if consulted_counts.get("dev", 0) == 0:
                        decision.target_agent = "dev"
                        decision.instruction_for_target = (
                            "提出前チェック: エンジニア視点が不足。"
                            "実装工数・技術リスク・実現方法を具体化してください。"
                        )
                    elif consulted_counts.get("marketing", 0) == 0:
                        decision.target_agent = "marketing"
                        decision.instruction_for_target = (
                            "提出前チェック: マーケ視点が不足。"
                            "拡散性・ターゲット適合・訴求差分を具体化してください。"
                        )
                    else:
                        decision.target_agent = "dev"
                        decision.instruction_for_target = (
                            "提出前チェック: 比較検討が不足。"
                            "候補2案のトレードオフを、実装負荷と集客インパクトで対比して提示してください。"
                        )

            # divergence phase must include engineer voice at least once.
            if (
                decision.next_action == "CALL_AGENT"
                and current_turn <= 4
                and consulted_counts.get("dev", 0) == 0
            ):
                decision.target_agent = "dev"
                trace_entry["guardrails"].append("force_dev_in_divergence")
                base_instruction = (decision.instruction_for_target or "").strip()
                mandatory_dev_prompt = (
                    "発散フェーズ要件: エンジニア視点を必ず追加してください。"
                    "実装コスト、技術ギミック、難所回避案を含めて回答してください。"
                )
                decision.instruction_for_target = (
                    f"{base_instruction}\n{mandatory_dev_prompt}"
                    if base_instruction
                    else mandatory_dev_prompt
                )

            trace_entry["pm_phase_final"] = decision.phase
            trace_entry["next_action_final"] = decision.next_action
            trace_entry["target_final"] = decision.target_agent
            self.last_meeting_trace.append(trace_entry)

            if decision.next_action == "FINISH_FOR_PRESIDENT":
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


def build_president_final_message(president_mention: str, decision: PMDecision) -> str:
    final_category = (decision.final_category or "未定").strip() or "未定"
    final_recommendation = (
        (decision.final_recommendation or "").strip()
        or "（提案内容が未設定です）"
    )
    revision_guidance = (
        (decision.revision_guidance or "").strip()
        or "改善の方向性を明確にして、再度検討してください。"
    )

    return (
        "---\n"
        f"{president_mention} 最終提案を提示しますね！\n\n"
        "**【社長への最終提案】**\n"
        f"・**カテゴリ:** {final_category}\n"
        f"・**提案概要:** {final_recommendation}\n"
        f"・**修正ガイドライン（NoGo時）:** {revision_guidance}\n"
        "---"
    )


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

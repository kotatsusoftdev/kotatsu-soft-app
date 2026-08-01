import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

from artifact_naming import build_artifact_stem
from config import ConfigError, Config, get_config
from meeting_chat_log import MeetingChatLogWriter, normalize_plain_text
from orchestrator import DynamicOrchestrator, ProposalSelectView, build_president_final_message
from market_research import MarketResearchError, MarketResearcher
from post_mortem import LessonUpdate, format_lesson_items, run_post_mortem
from spec_link_registry import get_latest_linked_game
from theme_proposal import (
    ThemeProposalError,
    ThemeProposer,
    build_meeting_theme_text,
)
from agents.dev.agent import DevAgent
from agents.marketing.agent import MarketingAgent
from agents.pm.agent import PMAgent


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    activity=discord.Activity(type=discord.ActivityType.watching, name="コタツで会議中"),
)


_meeting_guard_lock = asyncio.Lock()
_active_meeting_channel_ids: set[int] = set()
_post_mortem_lock = asyncio.Lock()
_post_mortem_running = False
_market_research_lock = asyncio.Lock()
_market_research_running = False
_config: Optional[Config] = None
_DISCORD_MESSAGE_LIMIT = 1900
_post_mortem_webhooks: dict[int, discord.Webhook] = {}
_AGENT_AVATAR_URLS = {
    "pm": (
        "https://raw.githubusercontent.com/kotatsusoftdev/kotatsu-soft-app/main/"
        "kotatsu-soft/ai-core/assets/avatars/pm.png"
    ),
    "dev": (
        "https://raw.githubusercontent.com/kotatsusoftdev/kotatsu-soft-app/main/"
        "kotatsu-soft/ai-core/assets/avatars/dev.png"
    ),
    "marketing": (
        "https://raw.githubusercontent.com/kotatsusoftdev/kotatsu-soft-app/main/"
        "kotatsu-soft/ai-core/assets/avatars/marketing.png"
    ),
}

_PERSISTENT_VIEW_STORE_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "logs" / "proposal_views.json"
)
_MEETING_TURN_AUDIT_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "logs" / "meeting_turn_audit.jsonl"
)


def _get_config_or_raise() -> Config:
    global _config
    if _config is None:
        _config = get_config()
    return _config


def _build_pm_agent(cfg: Config) -> PMAgent:
    workspace_root = Path(__file__).resolve().parent
    pm_config = workspace_root / "agents" / "pm" / "config.yaml"
    return PMAgent(
        api_key=cfg.GEMINI_API_KEY,
        config_path=str(pm_config),
        mention_id="@すずかちゃん(PM)",
    )


def _load_persistent_view_records() -> list[dict]:
    if not _PERSISTENT_VIEW_STORE_PATH.exists():
        return []
    try:
        raw = _PERSISTENT_VIEW_STORE_PATH.read_text(encoding="utf-8")
        if not raw.strip():
            return []
        payload = json.loads(raw)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[main] Failed to load persistent proposal views: {exc}")
    return []


def _save_persistent_view_records(records: list[dict]) -> None:
    _PERSISTENT_VIEW_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PERSISTENT_VIEW_STORE_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _register_persistent_view_record(record: dict) -> None:
    records = [r for r in _load_persistent_view_records() if r.get("message_id") != record.get("message_id")]
    records.append(record)
    _save_persistent_view_records(records)


def _append_meeting_turn_audit_record(
    *,
    channel_id: int,
    theme: str,
    revision_guidance: Optional[str],
    trace: list[dict],
    history: list[str],
    final_decision: Optional[object],
) -> None:
    if not trace:
        return

    violations: list[str] = []
    if any(item.get("next_action_initial") == "FINISH_FOR_PRESIDENT" and item.get("turn", 0) <= 5 for item in trace):
        violations.append("early_finish_attempt_before_turn6")
    if any(item.get("pm_phase_final") != item.get("expected_phase") for item in trace):
        violations.append("phase_mismatch_after_guardrail")

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channel_id": channel_id,
        "theme": theme,
        "revision_guidance": revision_guidance,
        "turn_count": len(trace),
        "history_length": len(history),
        "has_final_decision": final_decision is not None,
        "violations": violations,
        "trace": trace,
    }

    _MEETING_TURN_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _MEETING_TURN_AUDIT_PATH.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")


async def _restore_persistent_views() -> None:
    records = _load_persistent_view_records()
    if not records:
        return

    cfg = _get_config_or_raise()
    pm_agent = _build_pm_agent(cfg)
    restored = 0
    valid_records: list[dict] = []

    for record in records:
        try:
            message_id = int(record["message_id"])
            channel_id = int(record["channel_id"])
            theme = str(record["theme"])
            final_recommendation = str(record["final_recommendation"])
            final_category = str(record.get("final_category") or "未定")
            revision_guidance = str(record.get("revision_guidance") or "改善の方向性を明確にして、再度検討してください。")
            artifact_stem = str(record.get("artifact_stem") or "").strip() or None
            meeting_log_file = str(record.get("meeting_log_file") or "").strip()
            meeting_log_path = None
            if meeting_log_file:
                meeting_log_path = Path(__file__).resolve().parents[2] / "shared" / "meeting" / meeting_log_file
            proposal_message_id = str(record.get("proposal_message_id") or "").strip() or None
        except (KeyError, TypeError, ValueError):
            continue

        meeting_channel = bot.get_channel(channel_id)
        if meeting_channel is None:
            try:
                meeting_channel = await bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                meeting_channel = None

        if not isinstance(meeting_channel, discord.TextChannel):
            continue

        view = ProposalSelectView(
            final_recommendation=final_recommendation,
            final_category=final_category,
            revision_guidance=revision_guidance,
            pm_agent=pm_agent,
            meeting_channel=meeting_channel,
            theme=theme,
            rerun_meeting=run_meeting_round,
            artifact_stem=artifact_stem,
            meeting_log_path=meeting_log_path,
            proposal_message_id=proposal_message_id,
        )
        bot.add_view(view, message_id=message_id)
        restored += 1
        valid_records.append(record)

    if len(valid_records) != len(records):
        _save_persistent_view_records(valid_records)

    print(f"[main] Restored {restored} persistent proposal view(s).")


async def _try_reserve_meeting_channel(channel_id: int) -> bool:
    async with _meeting_guard_lock:
        if channel_id in _active_meeting_channel_ids:
            return False
        _active_meeting_channel_ids.add(channel_id)
        return True


async def _release_meeting_channel(channel_id: int) -> None:
    async with _meeting_guard_lock:
        _active_meeting_channel_ids.discard(channel_id)


async def _try_begin_post_mortem() -> bool:
    global _post_mortem_running
    async with _post_mortem_lock:
        if _post_mortem_running:
            return False
        _post_mortem_running = True
        return True


async def _end_post_mortem() -> None:
    global _post_mortem_running
    async with _post_mortem_lock:
        _post_mortem_running = False


async def _try_begin_market_research() -> bool:
    global _market_research_running
    async with _market_research_lock:
        if _market_research_running:
            return False
        _market_research_running = True
        return True


async def _end_market_research() -> None:
    global _market_research_running
    async with _market_research_lock:
        _market_research_running = False


def _chunk_discord_text(text: str, limit: int = _DISCORD_MESSAGE_LIMIT) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


def build_post_mortem_proposal_message(latest: dict) -> str:
    title = str(latest.get("game_title") or "").strip() or "(タイトル未設定)"
    return (
        "直近のゲームで反省会を始めますか？\n"
        f"- game_id: `{latest.get('game_id')}`\n"
        f"- タイトル: {title}\n"
        f"- artifact_stem: `{latest.get('artifact_stem')}`"
    )


def format_post_mortem_system_messages(warnings: list[str]) -> list[str]:
    messages: list[str] = ["✅ 反省会が完了しました。各AI社員が教訓を発表します。"]
    if warnings:
        warn_body = "⚠️ 警告:\n" + "\n".join(f"- {warning}" for warning in warnings)
        messages.extend(_chunk_discord_text(warn_body))
    return messages


def format_post_mortem_agent_message(item: LessonUpdate) -> str:
    speech = (item.speech or "").strip()
    if speech:
        return speech
    return (
        "今回の反省会で、次の教訓を得たよ。\n\n"
        f"{format_lesson_items(item.after)}"
    )


async def _get_post_mortem_webhook(
    channel: discord.TextChannel,
) -> Optional[discord.Webhook]:
    cached = _post_mortem_webhooks.get(channel.id)
    if cached:
        return cached

    webhook_name = DynamicOrchestrator.WEBHOOK_NAME
    try:
        existing = await channel.webhooks()
        reusable = next((wh for wh in existing if wh.name == webhook_name), None)
        if reusable:
            _post_mortem_webhooks[channel.id] = reusable
            return reusable
    except (discord.Forbidden, discord.HTTPException):
        pass

    try:
        created = await channel.create_webhook(
            name=webhook_name,
            reason="Use webhook to render distinct AI agent voices in Discord",
        )
        _post_mortem_webhooks[channel.id] = created
        return created
    except (discord.Forbidden, discord.HTTPException):
        return None


async def _post_as_agent(
    channel: discord.TextChannel,
    *,
    username: str,
    avatar_url: str,
    content: str,
) -> None:
    chunks = _chunk_discord_text(content)
    webhook = await _get_post_mortem_webhook(channel)
    if webhook and webhook.token:
        try:
            async with aiohttp.ClientSession() as session:
                dynamic_webhook = discord.Webhook.from_url(webhook.url, session=session)
                for chunk in chunks:
                    await dynamic_webhook.send(
                        content=chunk,
                        username=username,
                        avatar_url=avatar_url,
                    )
            return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            _post_mortem_webhooks.pop(channel.id, None)
            print(f"[main] post-mortem webhook send failed, fallback: {exc}")

    for chunk in chunks:
        await channel.send(f"**{username}**\n{chunk}")


async def publish_post_mortem_results(
    channel: discord.TextChannel,
    updates: list[LessonUpdate],
    warnings: list[str],
) -> None:
    for content in format_post_mortem_system_messages(warnings):
        await channel.send(content)

    for item in updates:
        avatar_url = _AGENT_AVATAR_URLS.get(item.role, _AGENT_AVATAR_URLS["pm"])
        await _post_as_agent(
            channel,
            username=item.name,
            avatar_url=avatar_url,
            content=format_post_mortem_agent_message(item),
        )


def build_market_research_proposal_message() -> str:
    return (
        "ヂャイアンに市場調査（トレンド＋ゲームメカニクス）をやらせますか？\n"
        "- 出力: `shared/research/latest_trends.json` / `mechanics_db.json`\n"
        "- 所要: 外部API呼び出しあり（数十秒〜数分）"
    )


def format_market_research_summary(trends: dict, mechanics: dict) -> str:
    trend_items = trends.get("trends") if isinstance(trends, dict) else None
    if not isinstance(trend_items, list):
        trend_items = []
    mech_items = mechanics.get("mechanics") if isinstance(mechanics, dict) else None
    if not isinstance(mech_items, list):
        mech_items = []

    lines = [
        "おう、のぶ太！オレ様が市場調査をぶちかましてやったぜ！",
        "",
        f"トレンド: {len(trend_items)}件（上書き）",
        f"メカニクスDB: 合計 {mechanics.get('total_count', len(mech_items))}件",
        f"データソース: {trends.get('data_source') or '（不明）'}",
        "",
        "【トレンド上位】",
    ]
    for item in trend_items[:5]:
        if not isinstance(item, dict):
            continue
        keyword = item.get("keyword") if isinstance(item.get("keyword"), dict) else {}
        original = str(keyword.get("original") or "").strip()
        abstracted = str(keyword.get("abstracted") or "").strip()
        platform = str(item.get("sns_platform") or "Hybrid")
        viral = item.get("viral_score", "?")
        label = abstracted or original or "(無題)"
        lines.append(f"- [{platform}] {label} (viral={viral})")
        if original and original != label:
            short_original = original if len(original) <= 80 else original[:80] + "…"
            lines.append(f"  元: {short_original}")

    lines.append("")
    lines.append("【メカニクス（代表）】")
    if not mech_items:
        lines.append("- （なし）")
    else:
        # 新しいもの優先で末尾から
        for item in list(mech_items)[-5:]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip() or "(無題)"
            insp = item.get("inspiration") if isinstance(item.get("inspiration"), dict) else {}
            motif = str(insp.get("unique_motif") or "").strip()
            if motif:
                lines.append(f"- {name} / {motif}")
            else:
                lines.append(f"- {name}")

    lines.append("")
    lines.append(
        "詳細は `shared/research/latest_trends.json` と "
        "`shared/research/mechanics_db.json` を見やがれ！"
    )
    return "\n".join(lines)


async def publish_market_research_results(
    channel: discord.TextChannel,
    trends: dict,
    mechanics: dict,
) -> None:
    await channel.send("✅ 市場調査が完了しました。ヂャイアンが概要を報告します。")
    await _post_as_agent(
        channel,
        username="ヂャイアン(マーケ)",
        avatar_url=_AGENT_AVATAR_URLS["marketing"],
        content=format_market_research_summary(trends, mechanics),
    )


class MarketResearchConfirmView(discord.ui.View):
    def __init__(self, *, api_key: str, results_channel_id: int):
        super().__init__(timeout=600)
        self.api_key = api_key
        self.results_channel_id = results_channel_id

        start_button = discord.ui.Button(
            label="はじめる",
            style=discord.ButtonStyle.success,
            custom_id="market_research_start",
        )
        start_button.callback = self._on_start
        self.add_item(start_button)

        cancel_button = discord.ui.Button(
            label="やめる",
            style=discord.ButtonStyle.secondary,
            custom_id="market_research_cancel",
        )
        cancel_button.callback = self._on_cancel
        self.add_item(cancel_button)

    def _disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self._disable_buttons()
        if interaction.message is not None:
            await interaction.message.edit(view=self)
        await interaction.response.send_message("キャンセルしました。", ephemeral=False)
        self.stop()

    async def _on_start(self, interaction: discord.Interaction) -> None:
        reserved = await _try_begin_market_research()
        if not reserved:
            await interaction.response.send_message(
                "⏳ 市場調査はすでに実行中です。完了までお待ちください。",
                ephemeral=False,
            )
            return

        self._disable_buttons()
        if interaction.message is not None:
            await interaction.message.edit(view=self)

        await interaction.response.send_message(
            "🔄 ヂャイアンが市場調査中です…（Yahoo / Togetter / Tavily / unityroom）",
            ephemeral=False,
        )

        try:
            results_channel = await resolve_text_channel(self.results_channel_id)
            if results_channel is None:
                await interaction.followup.send(
                    "⚠️ 市場調査チャンネルが見つかりません。"
                    "MARKET_RESEARCH_CHANNEL_ID の設定を確認してください。"
                )
                return

            def _run() -> tuple[dict, dict]:
                researcher = MarketResearcher(gemini_api_key=self.api_key)
                return researcher.run_all()

            trends, mechanics = await asyncio.to_thread(_run)
            await publish_market_research_results(results_channel, trends, mechanics)
        except MarketResearchError as exc:
            await interaction.followup.send(f"[market_research] 失敗しました: {exc}")
        except Exception as exc:
            await interaction.followup.send(f"[market_research] 失敗しました: {exc}")
        finally:
            await _end_market_research()
            self.stop()


class PostMortemConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        game_id: str,
        game_title: str,
        artifact_stem: str,
        game_path: str,
        api_key: str,
        results_channel_id: int,
    ):
        super().__init__(timeout=600)
        self.game_id = game_id
        self.game_title = game_title
        self.artifact_stem = artifact_stem
        self.game_path = game_path
        self.api_key = api_key
        self.results_channel_id = results_channel_id

        start_button = discord.ui.Button(
            label="はじめる",
            style=discord.ButtonStyle.success,
            custom_id="post_mortem_start",
        )
        start_button.callback = self._on_start
        self.add_item(start_button)

        cancel_button = discord.ui.Button(
            label="やめる",
            style=discord.ButtonStyle.secondary,
            custom_id="post_mortem_cancel",
        )
        cancel_button.callback = self._on_cancel
        self.add_item(cancel_button)

    def _disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self._disable_buttons()
        if interaction.message is not None:
            await interaction.message.edit(view=self)
        await interaction.response.send_message("キャンセルしました。", ephemeral=False)
        self.stop()

    async def _on_start(self, interaction: discord.Interaction) -> None:
        reserved = await _try_begin_post_mortem()
        if not reserved:
            await interaction.response.send_message(
                "⏳ 反省会はすでに実行中です。完了までお待ちください。",
                ephemeral=False,
            )
            return

        self._disable_buttons()
        if interaction.message is not None:
            await interaction.message.edit(view=self)

        await interaction.response.send_message(
            f"🔄 `{self.game_id}` の教訓を更新中です…",
            ephemeral=False,
        )

        try:
            results_channel = await resolve_text_channel(self.results_channel_id)
            if results_channel is None:
                await interaction.followup.send(
                    "⚠️ 反省会チャンネルが見つかりません。"
                    "POST_MORTEM_CHANNEL_ID の設定を確認してください。"
                )
                return

            updates, warnings = await asyncio.to_thread(
                run_post_mortem,
                artifact_stem=self.artifact_stem,
                api_key=self.api_key,
                game_path=self.game_path or None,
            )
            await publish_post_mortem_results(results_channel, updates, warnings)
        except Exception as exc:
            await interaction.followup.send(f"[post_mortem] 失敗しました: {exc}")
        finally:
            await _end_post_mortem()
            self.stop()


async def resolve_text_channel(channel_id: int) -> Optional[discord.TextChannel]:
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None
    if isinstance(channel, discord.TextChannel):
        return channel
    return None


async def resolve_meeting_channel() -> Optional[discord.TextChannel]:
    cfg = _get_config_or_raise()
    return await resolve_text_channel(cfg.MEETING_CHANNEL_ID)


async def resolve_post_mortem_channel() -> Optional[discord.TextChannel]:
    cfg = _get_config_or_raise()
    return await resolve_text_channel(cfg.POST_MORTEM_CHANNEL_ID)


async def resolve_market_research_channel() -> Optional[discord.TextChannel]:
    cfg = _get_config_or_raise()
    return await resolve_text_channel(cfg.MARKET_RESEARCH_CHANNEL_ID)


async def start_meeting_from_theme(
    theme: str,
    *,
    order_channel: discord.abc.Messageable,
    already_reserved: bool = False,
    meeting_channel: Optional[discord.TextChannel] = None,
) -> None:
    channel = meeting_channel or await resolve_meeting_channel()
    if channel is None:
        await order_channel.send(
            "⚠️ 企画検討チャンネルが見つかりません。MEETING_CHANNEL_ID の設定を確認してください。"
        )
        return

    if not already_reserved:
        reserved = await _try_reserve_meeting_channel(channel.id)
        if not reserved:
            await channel.send(
                "⏳ 企画検討はすでに進行中です。現在の会議が終わるまでお待ちください。"
            )
            await order_channel.send(
                f"⏳ 企画検討はすでに進行中です。企画検討チャンネル <#{channel.id}> を確認してください。"
            )
            return

    short_theme = theme.strip().splitlines()[0] if theme.strip() else theme
    await channel.send(
        f"📥 了解しました。テーマ「{short_theme}」でただちにPM AIへ仕様策定を回します。"
    )
    try:
        await run_meeting_round(theme, channel)
    finally:
        await _release_meeting_channel(channel.id)


async def propose_post_mortem(
    *,
    order_channel: discord.abc.Messageable,
    cfg: Config,
) -> None:
    async with _post_mortem_lock:
        if _post_mortem_running:
            await order_channel.send("⏳ 反省会実行中です。完了までお待ちください。")
            return

    results_channel = await resolve_post_mortem_channel()
    if results_channel is None:
        await order_channel.send(
            "⚠️ 反省会チャンネルが見つかりません。"
            "POST_MORTEM_CHANNEL_ID の設定を確認してください。"
        )
        return

    latest = get_latest_linked_game()
    if latest is None:
        await order_channel.send(
            "⚠️ 紐づいたゲームがありません。"
            "`spec_game_links.json` で仕様書とゲームをリンクしてから再度送ってください。"
        )
        return

    async with _post_mortem_lock:
        if _post_mortem_running:
            await order_channel.send("⏳ 反省会実行中です。完了までお待ちください。")
            return

    view = PostMortemConfirmView(
        game_id=str(latest["game_id"]),
        game_title=str(latest.get("game_title") or ""),
        artifact_stem=str(latest["artifact_stem"]),
        game_path=str(latest.get("game_path") or ""),
        api_key=cfg.GEMINI_API_KEY,
        results_channel_id=results_channel.id,
    )
    await order_channel.send(
        f"反省会チャンネル <#{results_channel.id}> で確認してください。"
    )
    await results_channel.send(build_post_mortem_proposal_message(latest), view=view)


async def propose_market_research(
    *,
    order_channel: discord.abc.Messageable,
    cfg: Config,
) -> None:
    async with _market_research_lock:
        if _market_research_running:
            await order_channel.send("⏳ 市場調査実行中です。完了までお待ちください。")
            return

    results_channel = await resolve_market_research_channel()
    if results_channel is None:
        await order_channel.send(
            "⚠️ 市場調査チャンネルが見つかりません。"
            "MARKET_RESEARCH_CHANNEL_ID の設定を確認してください。"
        )
        return

    async with _market_research_lock:
        if _market_research_running:
            await order_channel.send("⏳ 市場調査実行中です。完了までお待ちください。")
            return

    view = MarketResearchConfirmView(
        api_key=cfg.GEMINI_API_KEY,
        results_channel_id=results_channel.id,
    )
    await order_channel.send(
        f"市場調査チャンネル <#{results_channel.id}> で確認してください。"
    )
    await results_channel.send(build_market_research_proposal_message(), view=view)


def format_theme_options_agent_message(payload: dict) -> str:
    options = payload.get("options") if isinstance(payload, dict) else None
    if not isinstance(options, list):
        options = []
    lines = [
        "おう、のぶ太！オレ様がトレンド×ゲーム性でバカゲー企画をぶち上げてやったぜ！",
        "気に入った案を選べ。イマイチなら『もう一度検討』か『フリー入力』だ！",
        "",
    ]
    for opt in options:
        if not isinstance(opt, dict):
            continue
        oid = opt.get("option_id", "?")
        title = str(opt.get("title") or "").strip()
        summary = str(opt.get("concept_summary") or "").strip()
        viral = str(opt.get("viral_point") or "").strip()
        sources = (
            opt.get("combined_sources")
            if isinstance(opt.get("combined_sources"), dict)
            else {}
        )
        lines.append(f"**案{oid}: {title}**")
        if summary:
            lines.append(summary)
        if viral:
            lines.append(f"バズ: {viral}")
        trend_label = str(sources.get("trend_label") or "").strip()
        mech_label = str(sources.get("mechanic_label") or "").strip()
        if trend_label or mech_label:
            lines.append(f"掛け合わせ: {trend_label} × {mech_label}")
        lines.append("")
    return "\n".join(lines).strip()


async def publish_theme_options(
    channel: discord.TextChannel,
    payload: dict,
    *,
    api_key: str,
) -> None:
    await channel.send(
        "🎯 企画テーマ案です。会議に進む案を選ぶか、再検討／フリー入力／中止を選んでください。"
    )
    await _post_as_agent(
        channel,
        username="ヂャイアン(マーケ)",
        avatar_url=_AGENT_AVATAR_URLS["marketing"],
        content=format_theme_options_agent_message(payload),
    )
    options = payload.get("options") if isinstance(payload.get("options"), list) else []
    view = ThemeOptionSelectView(
        options=[o for o in options if isinstance(o, dict)],
        api_key=api_key,
        meeting_channel_id=channel.id,
    )
    await channel.send("どれで行く？", view=view)


async def propose_theme_options_for_meeting(
    *,
    order_channel: discord.abc.Messageable,
    cfg: Config,
    previous_titles: Optional[list[str]] = None,
    meeting_channel: Optional[discord.TextChannel] = None,
    already_reserved: bool = False,
) -> None:
    channel = meeting_channel or await resolve_meeting_channel()
    if channel is None:
        await order_channel.send(
            "⚠️ 企画検討チャンネルが見つかりません。MEETING_CHANNEL_ID の設定を確認してください。"
        )
        return

    if not already_reserved:
        reserved = await _try_reserve_meeting_channel(channel.id)
        if not reserved:
            await order_channel.send(
                f"⏳ 企画検討はすでに進行中です。企画検討チャンネル <#{channel.id}> を確認してください。"
            )
            return
        await order_channel.send(
            f"企画検討チャンネル <#{channel.id}> でテーマ案を確認してください。"
        )

    await channel.send("🔄 ヂャイアンがトレンド×ゲーム性でテーマ案を作成中です…")
    try:

        def _run() -> dict:
            proposer = ThemeProposer(gemini_api_key=cfg.GEMINI_API_KEY)
            return proposer.generate(previous_titles=previous_titles or [])

        payload = await asyncio.to_thread(_run)
        await publish_theme_options(channel, payload, api_key=cfg.GEMINI_API_KEY)
    except ThemeProposalError as exc:
        await channel.send(f"[theme_proposal] 失敗しました: {exc}")
        await _release_meeting_channel(channel.id)
    except Exception as exc:
        await channel.send(f"[theme_proposal] 失敗しました: {exc}")
        await _release_meeting_channel(channel.id)


class ThemeOptionSelectView(discord.ui.View):
    def __init__(
        self,
        *,
        options: list[dict],
        api_key: str,
        meeting_channel_id: int,
    ):
        super().__init__(timeout=900)
        self.options = options
        self.api_key = api_key
        self.meeting_channel_id = meeting_channel_id
        self._resolved = False
        # True の間は View が会議チャンネル予約を保持（timeout / 中止で解放）
        self._holds_reservation = True

        for opt in options[:4]:
            oid = int(opt.get("option_id") or 0)
            button = discord.ui.Button(
                label=f"案{oid}"[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"theme_option_{oid}",
            )
            button.callback = self._make_option_callback(opt)
            self.add_item(button)

        regen = discord.ui.Button(
            label="もう一度検討",
            style=discord.ButtonStyle.secondary,
            custom_id="theme_option_regen",
        )
        regen.callback = self._on_regen
        self.add_item(regen)

        free_input = discord.ui.Button(
            label="フリー入力",
            style=discord.ButtonStyle.secondary,
            custom_id="theme_option_free",
        )
        free_input.callback = self._on_free_input
        self.add_item(free_input)

        abort = discord.ui.Button(
            label="中止",
            style=discord.ButtonStyle.danger,
            custom_id="theme_option_abort",
        )
        abort.callback = self._on_abort
        self.add_item(abort)

    def _disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    def _make_option_callback(self, option: dict):
        async def _callback(interaction: discord.Interaction) -> None:
            if self._resolved:
                await interaction.response.send_message(
                    "この選択はすでに処理済みです。", ephemeral=True
                )
                return
            self._resolved = True
            self._disable_buttons()
            if interaction.message is not None:
                await interaction.message.edit(view=self)

            theme = build_meeting_theme_text(option)
            title = str(option.get("title") or "選択案")
            await interaction.response.send_message(
                f"✅ 「{title}」で企画会議を開始します。",
                ephemeral=False,
            )
            meeting_channel = await resolve_text_channel(self.meeting_channel_id)
            order_channel = interaction.channel or meeting_channel
            if meeting_channel is None or order_channel is None:
                await interaction.followup.send("⚠️ チャンネルを解決できませんでした。")
                await self._release_if_held()
                self.stop()
                return
            self._holds_reservation = False
            await start_meeting_from_theme(
                theme,
                order_channel=order_channel,
                already_reserved=True,
                meeting_channel=meeting_channel,
            )
            self.stop()

        return _callback

    async def _release_if_held(self) -> None:
        if self._holds_reservation:
            self._holds_reservation = False
            await _release_meeting_channel(self.meeting_channel_id)

    async def _on_regen(self, interaction: discord.Interaction) -> None:
        if self._resolved:
            await interaction.response.send_message(
                "この選択はすでに処理済みです。", ephemeral=True
            )
            return
        self._resolved = True
        self._disable_buttons()
        if interaction.message is not None:
            await interaction.message.edit(view=self)

        await interaction.response.send_message(
            "🔁 了解。ヂャイアンにもう一度検討させます…",
            ephemeral=False,
        )
        previous_titles = [
            str(o.get("title") or "") for o in self.options if o.get("title")
        ]
        meeting_channel = await resolve_text_channel(self.meeting_channel_id)
        if meeting_channel is None:
            await interaction.followup.send("⚠️ 企画検討チャンネルが見つかりません。")
            await self._release_if_held()
            self.stop()
            return
        try:
            cfg = _get_config_or_raise()
        except Exception:
            cfg = Config(
                DISCORD_TOKEN="",
                PRESIDENT_ORDER_CHANNEL_ID=0,
                GEMINI_API_KEY=self.api_key,
                MEETING_CHANNEL_ID=self.meeting_channel_id,
                POST_MORTEM_CHANNEL_ID=0,
                MARKET_RESEARCH_CHANNEL_ID=0,
                PRESIDENT_MENTION="",
            )
        # 予約は次の提示 View に引き継ぐ
        self._holds_reservation = False
        await propose_theme_options_for_meeting(
            order_channel=meeting_channel,
            cfg=cfg,
            previous_titles=previous_titles,
            meeting_channel=meeting_channel,
            already_reserved=True,
        )
        self.stop()

    async def _on_free_input(self, interaction: discord.Interaction) -> None:
        if self._resolved:
            await interaction.response.send_message(
                "この選択はすでに処理済みです。", ephemeral=True
            )
            return
        self._resolved = True
        self._disable_buttons()
        if interaction.message is not None:
            await interaction.message.edit(view=self)
        await interaction.response.send_modal(
            MeetingThemeModal(
                already_reserved=True,
                meeting_channel_id=self.meeting_channel_id,
                parent_view=self,
            )
        )
        # モーダルを閉じた場合は View の timeout で予約解放する（stop しない）

    async def _on_abort(self, interaction: discord.Interaction) -> None:
        if self._resolved:
            await interaction.response.send_message(
                "この選択はすでに処理済みです。", ephemeral=True
            )
            return
        self._resolved = True
        self._disable_buttons()
        if interaction.message is not None:
            await interaction.message.edit(view=self)
        await interaction.response.send_message(
            "🛑 企画会議を中止しました。",
            ephemeral=False,
        )
        await self._release_if_held()
        self.stop()

    async def on_timeout(self) -> None:
        await self._release_if_held()
        self._disable_buttons()


class MeetingThemeModal(discord.ui.Modal, title="企画会議のテーマ"):
    def __init__(
        self,
        *,
        already_reserved: bool = False,
        meeting_channel_id: Optional[int] = None,
        parent_view: Optional[discord.ui.View] = None,
    ) -> None:
        super().__init__()
        self.already_reserved = already_reserved
        self.meeting_channel_id = meeting_channel_id
        self.parent_view = parent_view
        self.theme_input = discord.ui.TextInput(
            label="テーマ",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            placeholder="例: テトリスと猫を掛け合わせたゲームを作って",
        )
        self.add_item(self.theme_input)

    def _stop_parent(self) -> None:
        if self.parent_view is not None:
            if hasattr(self.parent_view, "_holds_reservation"):
                self.parent_view._holds_reservation = False
            self.parent_view.stop()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        theme = self.theme_input.value.strip()
        if not theme:
            await interaction.response.send_message(
                "⚠️ テーマが空です。もう一度やり直してください。",
                ephemeral=False,
            )
            if self.already_reserved and self.meeting_channel_id is not None:
                await _release_meeting_channel(self.meeting_channel_id)
            self._stop_parent()
            return

        meeting_channel = None
        if self.meeting_channel_id is not None:
            meeting_channel = await resolve_text_channel(self.meeting_channel_id)
        if meeting_channel is None:
            meeting_channel = await resolve_meeting_channel()
        if meeting_channel is None:
            await interaction.response.send_message(
                "⚠️ 企画検討チャンネルが見つかりません。MEETING_CHANNEL_ID の設定を確認してください。",
                ephemeral=False,
            )
            if self.already_reserved and self.meeting_channel_id is not None:
                await _release_meeting_channel(self.meeting_channel_id)
            self._stop_parent()
            return

        await interaction.response.send_message(
            f"企画検討チャンネル <#{meeting_channel.id}> で開始します。",
            ephemeral=False,
        )
        order_channel = interaction.channel or meeting_channel
        try:
            await start_meeting_from_theme(
                theme,
                order_channel=order_channel,
                already_reserved=self.already_reserved,
                meeting_channel=meeting_channel,
            )
        finally:
            self._stop_parent()


class ProcessSelectView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=600)

        meeting_button = discord.ui.Button(
            label="企画会議",
            style=discord.ButtonStyle.primary,
            custom_id="process_select_meeting",
        )
        meeting_button.callback = self._on_meeting
        self.add_item(meeting_button)

        post_mortem_button = discord.ui.Button(
            label="反省会",
            style=discord.ButtonStyle.secondary,
            custom_id="process_select_post_mortem",
        )
        post_mortem_button.callback = self._on_post_mortem
        self.add_item(post_mortem_button)

        market_research_button = discord.ui.Button(
            label="市場調査",
            style=discord.ButtonStyle.success,
            custom_id="process_select_market_research",
        )
        market_research_button.callback = self._on_market_research
        self.add_item(market_research_button)

    def _disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    async def _on_meeting(self, interaction: discord.Interaction) -> None:
        self._disable_buttons()
        await interaction.response.edit_message(view=self)

        cfg = _get_config_or_raise()
        channel = interaction.channel
        if channel is None:
            await interaction.followup.send("⚠️ チャンネルを解決できませんでした。")
            self.stop()
            return

        await propose_theme_options_for_meeting(order_channel=channel, cfg=cfg)
        self.stop()

    async def _on_post_mortem(self, interaction: discord.Interaction) -> None:
        self._disable_buttons()
        await interaction.response.edit_message(view=self)

        cfg = _get_config_or_raise()
        channel = interaction.channel
        if channel is None:
            await interaction.followup.send("⚠️ チャンネルを解決できませんでした。")
            self.stop()
            return

        await propose_post_mortem(order_channel=channel, cfg=cfg)
        self.stop()

    async def _on_market_research(self, interaction: discord.Interaction) -> None:
        self._disable_buttons()
        await interaction.response.edit_message(view=self)

        cfg = _get_config_or_raise()
        channel = interaction.channel
        if channel is None:
            await interaction.followup.send("⚠️ チャンネルを解決できませんでした。")
            self.stop()
            return

        await propose_market_research(order_channel=channel, cfg=cfg)
        self.stop()


@bot.event
async def on_ready():
    print(f"[main] Bot is ready. Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="コタツで会議中")
    )
    await _restore_persistent_views()


async def run_meeting_round(
    theme: str,
    meeting_channel: discord.TextChannel,
    revision_guidance: Optional[str] = None,
) -> None:
    cfg = _get_config_or_raise()
    workspace_root = Path(__file__).resolve().parent
    pm_config = workspace_root / "agents" / "pm" / "config.yaml"
    dev_config = workspace_root / "agents" / "dev" / "config.yaml"
    marketing_config = workspace_root / "agents" / "marketing" / "config.yaml"

    pm_agent = PMAgent(
        api_key=cfg.GEMINI_API_KEY,
        config_path=str(pm_config),
        mention_id="@すずかちゃん(PM)",
    )
    dev_agent = DevAgent(
        api_key=cfg.GEMINI_API_KEY,
        config_path=str(dev_config),
        mention_id="@スゴ杉くん(エンジニア)",
    )
    marketing_agent = MarketingAgent(
        api_key=cfg.GEMINI_API_KEY,
        config_path=str(marketing_config),
        mention_id="@ヂャイアン(マーケ)",
    )

    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=pm_agent,
        other_agents={"dev": dev_agent, "marketing": marketing_agent},
        president_mention=cfg.PRESIDENT_MENTION,
    )

    artifact_stem = build_artifact_stem(theme)
    chat_log = MeetingChatLogWriter(artifact_stem=artifact_stem)
    chat_log.log_meeting_start(theme)
    if revision_guidance:
        chat_log.append(
            role="system",
            message=f"修正方針：{normalize_plain_text(revision_guidance)}",
            msg_type="system",
            phase="DIVERGENCE",
            turn=0,
        )
    chat_log.log_president_message(theme, turn=1)

    final_pm_speech, history, final_decision = await orchestrator.execute_meeting(
        theme,
        meeting_channel,
        revision_guidance=revision_guidance,
        chat_log=chat_log,
    )
    _append_meeting_turn_audit_record(
        channel_id=meeting_channel.id,
        theme=theme,
        revision_guidance=revision_guidance,
        trace=orchestrator.last_meeting_trace,
        history=history,
        final_decision=final_decision,
    )

    if final_decision is None:
        summary = (
            f"{cfg.PRESIDENT_MENTION} 最終提案を提示しますね！\n\n"
            "**【社長への最終提案】**\n"
            "・**カテゴリ:** 未定\n"
            "・**提案概要:** （提案内容が未設定です）\n"
            "・**修正ガイドライン（NoGo時）:** 改善の方向性を明確にして、再度検討してください。"
        )
        final_recommendation = ""
        final_category = "未定"
        revision_guidance_text = "改善の方向性を明確にして、再度検討してください。"
    else:
        summary = build_president_final_message(cfg.PRESIDENT_MENTION, final_decision)

        final_recommendation = (final_decision.final_recommendation or "").strip()
        final_category = (final_decision.final_category or "未定").strip() or "未定"
        revision_guidance_text = (
            (final_decision.revision_guidance or "").strip()
            or "改善の方向性を明確にして、再度検討してください。"
        )

    proposal_message_id: Optional[str] = None
    if final_decision is not None:
        proposal_lines = [
            "【最終提案】",
            f"カテゴリ：{final_category}",
            f"概要：{final_recommendation}",
            f"修正ガイドライン（NoGo時）：{revision_guidance_text}",
        ]
        proposal_message_id = chat_log.log_proposal(
            message="\n".join(proposal_lines),
            phase=final_decision.phase or "FINAL",
            turn=len(orchestrator.last_meeting_trace) or 1,
        )

    def chunk_text(text: str, limit: int = 2000) -> list[str]:
        chunks: list[str] = []
        while text:
            if len(text) <= limit:
                chunks.append(text)
                break
            split_at = text.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = limit
            chunks.append(text[:split_at])
            text = text[split_at:]
        return chunks

    view = ProposalSelectView(
        final_recommendation=final_recommendation,
        final_category=final_category,
        revision_guidance=revision_guidance_text,
        pm_agent=pm_agent,
        meeting_channel=meeting_channel,
        theme=theme,
        rerun_meeting=run_meeting_round,
        artifact_stem=artifact_stem,
        meeting_log_path=chat_log.path,
        proposal_message_id=proposal_message_id,
    )

    summary_chunks = chunk_text(summary)
    for chunk in summary_chunks[:-1]:
        await meeting_channel.send(content=chunk)
    summary_message = await meeting_channel.send(content=summary_chunks[-1], view=view)
    _register_persistent_view_record(
        {
            "message_id": summary_message.id,
            "channel_id": meeting_channel.id,
            "theme": theme,
            "final_recommendation": final_recommendation,
            "final_category": final_category,
            "revision_guidance": revision_guidance_text,
            "artifact_stem": artifact_stem,
            "meeting_log_file": chat_log.path.name,
            "proposal_message_id": proposal_message_id,
        }
    )


@bot.event
async def on_message(message: discord.Message):
    cfg = _get_config_or_raise()

    if message.author == bot.user or message.author.bot:
        return

    if message.channel.id == cfg.PRESIDENT_ORDER_CHANNEL_ID:
        print(f"[main] Received message in president order channel: {message.content}")
        await message.channel.send(
            "どのプロセスを開始しますか？",
            view=ProcessSelectView(),
        )

    await bot.process_commands(message)


def main() -> None:
    print("[main] Starting AI社員 Discord Bot...")
    global _config
    try:
        _config = get_config()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    bot.run(_config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()

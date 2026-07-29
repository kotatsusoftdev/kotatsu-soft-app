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
from post_mortem import LessonUpdate, format_lesson_items, run_post_mortem
from spec_link_registry import get_latest_linked_game
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
    return (
        "今回の反省会で、自分の教訓をこう更新したよ。\n\n"
        "**before**\n"
        f"{format_lesson_items(item.before)}\n\n"
        "**after**\n"
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


class PostMortemConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        game_id: str,
        game_title: str,
        artifact_stem: str,
        game_path: str,
        api_key: str,
    ):
        super().__init__(timeout=600)
        self.game_id = game_id
        self.game_title = game_title
        self.artifact_stem = artifact_stem
        self.game_path = game_path
        self.api_key = api_key

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
            f"🔄 `{self.game_id}` の教訓を更新中です…（少々お待ちください）",
            ephemeral=False,
        )

        try:
            updates, warnings = await asyncio.to_thread(
                run_post_mortem,
                artifact_stem=self.artifact_stem,
                api_key=self.api_key,
                game_path=self.game_path or None,
            )
            channel = interaction.channel
            if isinstance(channel, discord.TextChannel):
                await publish_post_mortem_results(channel, updates, warnings)
        except Exception as exc:
            await interaction.followup.send(f"[post_mortem] 失敗しました: {exc}")
        finally:
            await _end_post_mortem()
            self.stop()


async def handle_post_mortem_channel_message(message: discord.Message, cfg: Config) -> None:
    async with _post_mortem_lock:
        if _post_mortem_running:
            await message.channel.send("⏳ 反省会実行中です。完了までお待ちください。")
            return

    latest = get_latest_linked_game()
    if latest is None:
        await message.channel.send(
            "⚠️ 紐づいたゲームがありません。"
            "`spec_game_links.json` で仕様書とゲームをリンクしてから再度送ってください。"
        )
        return

    view = PostMortemConfirmView(
        game_id=str(latest["game_id"]),
        game_title=str(latest.get("game_title") or ""),
        artifact_stem=str(latest["artifact_stem"]),
        game_path=str(latest.get("game_path") or ""),
        api_key=cfg.GEMINI_API_KEY,
    )
    await message.channel.send(build_post_mortem_proposal_message(latest), view=view)


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

    if message.channel.id == cfg.MUCHABURI_CHANNEL_ID:
        print(f"[main] Received message in muchaburi channel: {message.content}")
        await message.channel.send("📥 了解しました。ただちにPM AIへ仕様策定を回します。")

        meeting_channel = bot.get_channel(cfg.MEETING_CHANNEL_ID)
        if meeting_channel is None:
            try:
                meeting_channel = await bot.fetch_channel(cfg.MEETING_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                meeting_channel = None

        if not isinstance(meeting_channel, discord.TextChannel):
            await message.channel.send(
                "⚠️ #コタツ会議室 が見つかりません。MEETING_CHANNEL_ID の設定を確認してください。"
            )
            return

        reserved = await _try_reserve_meeting_channel(meeting_channel.id)
        if not reserved:
            await message.channel.send(
                "⏳ 企画検討はすでに進行中です。現在の会議が終わるまでお待ちください。"
            )
            return

        try:
            await run_meeting_round(message.content, meeting_channel)
        finally:
            await _release_meeting_channel(meeting_channel.id)
    elif message.channel.id == cfg.POST_MORTEM_CHANNEL_ID:
        print(f"[main] Received message in post-mortem channel: {message.content}")
        await handle_post_mortem_channel_message(message, cfg)

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

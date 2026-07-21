import asyncio
import json
from pathlib import Path
import sys
from typing import Optional

import discord
from discord.ext import commands

from config import ConfigError, Config, get_config
from orchestrator import DynamicOrchestrator, ProposalSelectView
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
_config: Optional[Config] = None

_PERSISTENT_VIEW_STORE_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "logs" / "proposal_views.json"
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

    final_pm_speech, history, final_decision = await orchestrator.execute_meeting(
        theme,
        meeting_channel,
        revision_guidance=revision_guidance,
    )
    history_text = "\n".join(history[-10:]) if history else "議論ログはありません。"

    final_recommendation = (
        final_decision.final_recommendation if final_decision and final_decision.final_recommendation else ""
    )
    final_category = (
        final_decision.final_category if final_decision and final_decision.final_category else "未定"
    )
    revision_guidance_text = (
        final_decision.revision_guidance
        if final_decision and final_decision.revision_guidance
        else "改善の方向性を明確にして、再度検討してください。"
    )

    summary = (
        f"{cfg.PRESIDENT_MENTION} 会議が完了しました。\n"
        f"PM最終発言:\n{final_pm_speech}\n\n"
        "── 推奨1案 ──\n"
        f"{final_category}:\n{final_recommendation}\n\n"
        "── 直近の議論履歴 ──\n"
        f"{history_text}"
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

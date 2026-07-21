import asyncio
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from config import config
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


async def run_meeting_round(
    theme: str,
    meeting_channel: discord.TextChannel,
    revision_guidance: Optional[str] = None,
) -> None:
    workspace_root = Path(__file__).resolve().parent
    pm_config = workspace_root / "agents" / "pm" / "config.yaml"
    dev_config = workspace_root / "agents" / "dev" / "config.yaml"
    marketing_config = workspace_root / "agents" / "marketing" / "config.yaml"

    pm_agent = PMAgent(
        api_key=config.GEMINI_API_KEY,
        config_path=str(pm_config),
        mention_id="@みずかちゃん(PM)",
    )
    dev_agent = DevAgent(
        api_key=config.GEMINI_API_KEY,
        config_path=str(dev_config),
        mention_id="@タカ杉くん(エンジニア)",
    )
    marketing_agent = MarketingAgent(
        api_key=config.GEMINI_API_KEY,
        config_path=str(marketing_config),
        mention_id="@ジャイアン(マーケ)",
    )

    orchestrator = DynamicOrchestrator(
        webhook_url=None,
        pm_agent=pm_agent,
        other_agents={"dev": dev_agent, "marketing": marketing_agent},
        president_mention=config.PRESIDENT_MENTION,
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
        f"{config.PRESIDENT_MENTION} 会議が完了しました。\n"
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
    await meeting_channel.send(content=summary_chunks[-1], view=view)


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user or message.author.bot:
        return

    if message.channel.id == config.MUCHABURI_CHANNEL_ID:
        print(f"[main] Received message in muchaburi channel: {message.content}")
        await message.channel.send("📥 了解しました。ただちにPM AIへ仕様策定を回します。")

        meeting_channel = bot.get_channel(config.MEETING_CHANNEL_ID)
        if meeting_channel is None:
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
    bot.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()

from typing import Literal, Optional

from pydantic import BaseModel, Field


ALLOWED_PHASES = {"DIVERGENCE", "CONFLICT", "FINAL"}


class PMDecision(BaseModel):
    speech: str = Field(
        ...,
        description=(
            "Discordのチャット上に投稿する発言文。他メンバーや社長を指名する場合は '@ジャイアン(マーケ)' や '@タカ杉くん(エンジニア)' 等のメンションを含める。"
        ),
    )
    phase: Literal["DIVERGENCE", "CONFLICT", "FINAL"] = Field(
        ...,
        description=(
            "現在の会議フェーズ。ターンに応じて発散、衝突、最終集約のいずれかを表す。"
        ),
    )
    next_action: str = Field(
        ...,
        description=(
            "次に実行するアクション。議論を継続する場合は 'CALL_AGENT'、提案がまとまり社長に引き渡す場合は 'FINISH_FOR_PRESIDENT'."
        ),
    )
    target_agent: Optional[str] = Field(
        default=None,
        description="next_action が 'CALL_AGENT' の場合、次に発言させたい対象ロール。",
    )
    instruction_for_target: Optional[str] = Field(
        default=None,
        description="指名する対象エージェントに対する分析指示・質問内容。",
    )
    divergence_prompt: Optional[str] = Field(
        default=None,
        description="発散フェーズ時に追加で与える、よりアイデアを広げる指示。",
    )
    final_recommendation: Optional[str] = Field(
        default=None,
        description="社長に提示する、単一の推奨案概要。",
    )
    final_category: Optional[str] = Field(
        default=None,
        description="推奨案のカテゴリ名。",
    )
    revision_guidance: Optional[str] = Field(
        default=None,
        description="社長が NoGo を出した場合に、再検討するための修正方針。",
    )

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional
from google import genai
from google.genai import types

from agents.base_agent import BaseAgent
from agents.pm.schemas import PMDecision


class PMAgent(BaseAgent):
    def __init__(self, api_key: str, config_path: str, mention_id: str):
        super().__init__(
            config_path=config_path,
            avatar_url=(
                "https://raw.githubusercontent.com/kotatsusoftdev/kotatsu-soft-app/main/ai-core/assets/avatars/pm.png"
            ),
            mention_id=mention_id,
        )
        self.client = genai.Client(api_key=api_key)

    def _phase_label(self, current_turn: int, max_turns: int) -> str:
        if current_turn <= 4:
            return "DIVERGENCE"
        if current_turn <= 8:
            return "CONFLICT"
        return "FINAL"

    def _phase_instruction(self, current_turn: int, max_turns: int) -> str:
        phase = self._phase_label(current_turn, max_turns)
        if phase == "DIVERGENCE":
            return (
                "このフェーズでは、まずはたくさんの案を出してください。"
                "作れるかどうかはあとで考えていいので、面白い案や変わった案をどんどん出してください。"
                "ただし褒めるだけで終わらせず、各案に対して『1日で作れるか』『地味にならないか』『本当に面白いか』を必ず問い、"
                "次の人に別視点の反証を求めてください。"
            )
        if phase == "CONFLICT":
            return (
                "【重要・衝突フェーズ】このフェーズでは、PM自身が審判として鋭くツッコミを入れてください。"
                "マーケの大げさな案には『本当に1日で完成する根拠は？』『何を削れば成立する？』を、"
                "開発の安全すぎる案には『それで本当に盛り上がる？』『地味すぎない？』を必ず突きつけてください。"
                "案を並べるだけで終わらせず、トレードオフを明示し、残す要素と捨てる要素を明言して、"
                "現実的かつ最も魅力的な1点へ強い圧力で絞り込んでください。"
            )
        return (
            "この最終フェーズでは、3案を比較するのではなく、"
            "1案だけを推す形でまとめてください。"
            "final_recommendation には社長向けの1案の要約を、"
            "final_category にはその案のカテゴリ名を、"
            "revision_guidance には NoGo のときに直すべき方向を入れてください。"
            "最後に、社長に渡すために FINISH_FOR_PRESIDENT を返してください。"
        )

    async def think_and_reply(self, prompt: str, conversation_history: list[str]) -> str:
        decision = await self.decide_next_step(
            prompt,
            conversation_history,
            current_turn=1,
            max_turns=10,
        )
        return decision.speech

    async def decide_next_step(
        self,
        theme: str,
        history: list[str],
        current_turn: int,
        max_turns: int,
        force_final: bool = False,
        revision_guidance: Optional[str] = None,
    ) -> PMDecision:
        base_instruction = self.build_system_instruction()

        consulted_marketing = any(
            "ジャイアン(マーケ):" in line
            or "@ジャイアン(マーケ)" in line
            or "Gian_Agent:" in line
            or "@Gian_Agent" in line
            for line in history
        )
        consulted_dev = any(
            "タカ杉くん(エンジニア):" in line
            or "@タカ杉くん(エンジニア)" in line
            or "Takasugi_Agent:" in line
            or "@Takasugi_Agent" in line
            for line in history
        )
        phase_label = self._phase_label(current_turn, max_turns)
        phase_instruction = self._phase_instruction(current_turn, max_turns)
        final_phase = force_final or current_turn >= max_turns

        dynamic_instruction = (
            f"{base_instruction}\n"
            "【現在の議論状況と判定条件】\n"
            f"1. 現在のターン数: {current_turn} / 最大 {max_turns} ターン。\n"
            f"2. 現在の会議フェーズ: {phase_label}。{phase_instruction}\n"
            "3. まずは必ず ジャイアン(マーケ) と タカ杉くん(エンジニア) の両方に、違う見方で意見を出させてください。\n"
            "4. current_turn が max_turns より小さい場合、next_action は必ず 'CALL_AGENT' とし、FINISH_FOR_PRESIDENT を選ばないでください。\n"
            "5. current_turn が max_turns の場合のみ、next_action は 'FINISH_FOR_PRESIDENT' としてください。\n"
            "6. CALL_AGENT の場合、target_agent と instruction_for_target を必ず記載し、次の専門家に向けた具体的な問いを作成してください。\n"
            "7. speech では、今の進み具合を短くまとめつつ、必ず最低1つのリスク指摘または矛盾指摘を入れてください。\n"
            "8. speech は毎回 500文字前後（目安 400〜600文字）に収めてください。\n"
            "9. Turn 1〜4では、たくさんのアイデアを出すように促してください。\n"
            "10. Turn 5〜8では、作れるかどうかと面白いかどうかをぶつけて、残す案と捨てる案をはっきりさせてください。\n"
            "11. Turn 9〜10では最終案を1案に絞る準備を行い、Turn 10で必ず FINISH_FOR_PRESIDENT を返してください。\n"
            f"12. 現在の相談状況: ジャイアン(マーケ) に相談済み={consulted_marketing}, タカ杉くん(エンジニア) に相談済み={consulted_dev}。\n"
            "13. divergence_prompt には、さらに別の発想を広げる短い質問を入れてください。\n"
            "14. next_action が 'FINISH_FOR_PRESIDENT' の場合、JSON に final_recommendation / final_category / revision_guidance を含めてください。\n"
            "15. final_recommendation は推奨する1案の要約を、final_category はその案のカテゴリ名、revision_guidance は NoGo 時の修正方針を返してください。\n"
            "16. phase フィールドに現在のフェーズ名を返してください。\n"
            "17. メンバーの意見を受けるとき、単なる称賛（例: 素敵、ありがとう）だけで終えることを禁止し、必ず検証質問か反証を続けてください。\n"
            "18. 毎ターン、『本当に1日で作れるの？』『地味にならない？』『それ本当に面白い？』の3観点を少なくとも要約内で触れてください。\n"
            "19. 『全部盛り込み』の妥協は禁止。speech または instruction_for_target に、残す要素と捨てる要素を必ず明記してください。\n"
            "20. トレードオフ（あれを立てればこれが立たず）を毎ターン最低1つ示し、どちらを優先するかを明言してください。\n"
        )
        if revision_guidance:
            dynamic_instruction += (
                "21. 社長のNoGo修正方針が与えられている場合、"
                "その内容を最優先制約として扱い、"
                "次の問いかけ・比較観点・最終提案の全てに必ず反映してください。\n"
                "22. FINISH_FOR_PRESIDENT の際は、final_recommendation 内で"
                "修正方針をどう満たしたかを明記してください。\n"
            )
        if final_phase:
            dynamic_instruction += (
                "23. このターンでは必ず FINISH_FOR_PRESIDENT を返し、"
                "推奨する1案を明確にし、社長の Go/NoGo 判断に必要な情報を提出してください。\n"
            )

        prompt_text = (
            f"お題: {theme}\n"
            + (
                "社長のNoGo修正方針（最優先）:\n"
                f"{revision_guidance}\n\n"
                if revision_guidance
                else ""
            )
            +
            "これまでの議論履歴:\n"
            + ("\n".join(history) if history else "（議論未開始。初案の提示と担当者への振分けを行ってください）")
        )

        response_json_schema = {
            "type": "object",
            "properties": {
                "speech": {"type": "string"},
                "phase": {
                    "type": "string",
                    "enum": ["DIVERGENCE", "CONFLICT", "FINAL"],
                },
                "next_action": {
                    "type": "string",
                    "enum": ["CALL_AGENT"] if not final_phase else ["CALL_AGENT", "FINISH_FOR_PRESIDENT"],
                },
                "target_agent": {
                    "type": "string",
                    "enum": ["marketing", "dev"],
                },
                "instruction_for_target": {"type": "string"},
                "divergence_prompt": {"type": "string"},
                "final_recommendation": {"type": "string"},
                "final_category": {"type": "string"},
                "revision_guidance": {"type": "string"},
            },
            "required": ["speech", "phase", "next_action"] + (
                ["target_agent", "instruction_for_target"] if not final_phase else ["final_recommendation", "final_category", "revision_guidance"]
            ),
            "additionalProperties": False,
        }

        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model_name,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                system_instruction=dynamic_instruction,
                response_mime_type="application/json",
                responseJsonSchema=response_json_schema,
                temperature=0.4,
            ),
        )

        response_text = getattr(response, "text", None)
        if not response_text:
            response_text = self.extract_text_from_response(response)

        return PMDecision.model_validate_json(response_text)

    async def generate_spec_for_plan(
        self,
        selected_plan: str,
        proposal_summary: str,
    ) -> Path:
        instruction = (
            "あなたはPMエージェントです。採用された案の概要を受け取り、"
            "Discord上で提示した最終提案を元にMarkdown形式の詳細仕様書を作成してください。"
            "仕様書には【ゲームタイトル】【概要】【コア体験/ゲーム性】【操作方法】【開発手順（エンジニアAIへの指示用）】を含めてください。"
        )
        prompt_text = (
            f"採用された案: {selected_plan}\n"
            "以下はPMがまとめた最終提案の要約です。\n"
            f"{proposal_summary}\n\n"
            "上記を基に、Markdown形式の仕様書を作成してください。"
        )

        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model_name,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                system_instruction=instruction,
                response_mime_type="text/plain",
                temperature=self.temperature,
            ),
        )

        spec_text = self.extract_text_from_response(response).strip()
        repo_root = Path(__file__).resolve().parents[4]
        specs_dir = repo_root / "shared" / "specs"
        specs_dir.mkdir(parents=True, exist_ok=True)
        safe_plan_name = "".join(
            c if c.isalnum() or c in ("_", "-") else "_"
            for c in selected_plan
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        spec_file = specs_dir / f"spec_{safe_plan_name}_{timestamp}.md"
        spec_file.write_text(spec_text, encoding="utf-8")
        return spec_file

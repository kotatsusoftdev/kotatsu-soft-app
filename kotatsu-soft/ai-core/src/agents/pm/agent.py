import asyncio
from datetime import datetime
from pathlib import Path
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

    async def think_and_reply(self, prompt: str, conversation_history: list[str]) -> str:
        decision = await self.decide_next_step(
            prompt,
            conversation_history,
            current_turn=1,
            max_turns=5,
        )
        return decision.speech

    async def decide_next_step(
        self,
        theme: str,
        history: list[str],
        current_turn: int,
        max_turns: int,
    ) -> PMDecision:
        base_instruction = self.build_system_instruction()

        consulted_marketing = any("Marketing_Agent:" in line or "@marketing" in line for line in history)
        consulted_dev = any("Dev_Agent:" in line or "@dev" in line for line in history)

        dynamic_instruction = (
            f"{base_instruction}\n"
            "【現在の議論状況と判定条件】\n"
            f"1. 現在のターン数: {current_turn} / 最大 {max_turns} ターン。\n"
            "2. まずは必ず Marketing_Agent と Dev_Agent の両方を少なくとも1回ずつ呼び出し、それぞれの専門意見を取得してください。\n"
            "3. どちらか一方でも未取得の場合、next_action は必ず 'CALL_AGENT' とし、まだ相談していないエージェントを優先してください。\n"
            f"4. 現在の相談状況: Marketing_Agent に相談済み={consulted_marketing}, Dev_Agent に相談済み={consulted_dev}。\n"
            "5. 結論は最終ターンに近い段階でまとめ、十分な専門意見が集まっている場合にのみ 'FINISH_FOR_PRESIDENT' を選択してください。\n"
            "6. speech 内には対象エージェントのメンションIDを含めてください。\n"
            "7. next_action が 'FINISH_FOR_PRESIDENT' の場合、JSON に final_proposals と final_categories を含めてください。"
            "final_proposals は案A/B/Cの要約を、final_categories はPMが命名した各案のカテゴリー名をそれぞれ返してください。\n"
        )

        prompt_text = (
            f"お題: {theme}\n"
            "これまでの議論履歴:\n"
            + ("\n".join(history) if history else "（議論未開始。初案の提示と担当者への振分けを行ってください）")
        )

        response_json_schema = {
            "type": "object",
            "properties": {
                "speech": {"type": "string"},
                "next_action": {
                    "type": "string",
                    "enum": ["CALL_AGENT", "FINISH_FOR_PRESIDENT"],
                },
                "target_agent": {
                    "type": "string",
                    "enum": ["marketing", "dev"],
                },
                "instruction_for_target": {"type": "string"},
                "final_proposals": {
                    "type": "object",
                    "properties": {
                        "A": {"type": "string"},
                        "B": {"type": "string"},
                        "C": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "final_categories": {
                    "type": "object",
                    "properties": {
                        "A": {"type": "string"},
                        "B": {"type": "string"},
                        "C": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["speech", "next_action"],
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
                temperature=self.temperature,
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

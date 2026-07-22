import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional
from google import genai
from google.genai import types

from agents.base_agent import BaseAgent
from agents.pm.schemas import PMDecision


class PMAgent(BaseAgent):
    CALL_NAME_RULES = (
        "【呼称ルール】\n"
        "- 一人称は『私』を使う。\n"
        "- ヂャイアン（マーケ）への呼び方は『ヂャイアン』で統一する。\n"
        "- スゴ杉くん（エンジニア）への呼び方は『スゴ杉さん』で統一する。\n"
        "- 社長（ユーザー）への呼び方は『のぶ太社長』または『のぶ太さん』を使う。\n"
        "- メンバーに意見を振る・返答する際は、本文中やメンション（@呼び名）で自然に相手の名前を呼ぶ。\n"
        "- 上記以外の呼び名（あだ名・省略名）は使わない。\n"
    )

    def __init__(self, api_key: str, config_path: str, mention_id: str):
        super().__init__(
            config_path=config_path,
            avatar_url=(
                "https://raw.githubusercontent.com/kotatsusoftdev/kotatsu-soft-app/main/kotatsu-soft/ai-core/assets/avatars/pm.png"
            ),
            mention_id=mention_id,
        )
        self.client = genai.Client(api_key=api_key)

    def _phase_label(self, current_turn: int, max_turns: int) -> str:
        if current_turn <= 4:
            return "DIVERGENCE"
        if current_turn <= 7:
            return "CONFLICT"
        return "FINAL"

    def _phase_instruction(self, current_turn: int, max_turns: int) -> str:
        phase = self._phase_label(current_turn, max_turns)
        if phase == "DIVERGENCE":
            return (
                "Turn 1〜4 の発散フェーズです。"
                "この区間では合意・提出は禁止です。"
                "毎ターン、既出案と明確に違う切り口の別案を最低1つ追加し、"
                "必ずスゴ杉くん(エンジニア)にも実現可能性・実装負荷・技術ギミックの観点で問いを振ってください。"
            )
        if phase == "CONFLICT":
            return (
                "Turn 5〜7 の衝突フェーズです。"
                "複数案が出揃っている前提で、初めて比較・衝突に入ります。"
                "単に削るだけでなく、技術面(実装コスト/難易度)と集客面(拡散性/訴求)のトレードオフを明示してください。"
                "何を優先し、何を諦めるかを自然な言葉で整理してください。"
            )
        return (
            "Turn 8〜10 の最終集約フェーズです。"
            "比較を打ち切って1案に絞り、初見インパクト・拡散性・リトライしたくなるテンポを短く整理してください。"
            "FINISH_FOR_PRESIDENT を返し、final_recommendation / final_category / revision_guidance を必ず埋めてください。"
        )

    def _has_tradeoff_comparison(self, history: list[str]) -> bool:
        if not history:
            return False

        recent_text = "\n".join(history[-10:])
        strong_markers = (
            "トレードオフ",
            "比較",
            "二者択一",
            "案A",
            "案B",
            "メリット",
            "デメリット",
            "採用理由",
            "不採用理由",
            "優先順位",
        )
        decision_markers = (
            "どちら",
            "優先",
            "残す",
            "捨て",
            "削る",
            "諦め",
            "採用",
            "不採用",
        )
        has_strong = any(marker in recent_text for marker in strong_markers)
        has_decision = any(marker in recent_text for marker in decision_markers)
        has_explicit_two_options = "案A" in recent_text and "案B" in recent_text
        return has_explicit_two_options or (has_strong and has_decision)

    def _latest_marketing_message(self, history: list[str]) -> str:
        for line in reversed(history):
            if (
                "ヂャイアン(マーケ):" in line
                or "@ヂャイアン(マーケ)" in line
                or "Gian_Agent:" in line
                or "@Gian_Agent" in line
            ):
                return line
        return ""

    def _is_kitchen_sink_proposal(self, history: list[str]) -> bool:
        latest_marketing = self._latest_marketing_message(history)
        if not latest_marketing:
            return False

        markers = (
            "全部乗せ",
            "全部のせ",
            "全部盛り",
            "全部入り",
            "欲張り",
            "盛り込み",
            "あれもこれも",
            "全部",
            "詰め込み",
        )
        return any(marker in latest_marketing for marker in markers)

    def _looks_like_consensus(self, history: list[str]) -> bool:
        if not history:
            return False

        recent_text = "\n".join(history[-6:])
        consensus_markers = (
            "合意",
            "一致",
            "決定",
            "確定",
            "一本化",
            "これでいく",
            "この案でいく",
            "採用",
            "最終案",
        )
        return any(marker in recent_text for marker in consensus_markers)

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
        force_convergence: bool = False,
    ) -> PMDecision:
        base_instruction = self.build_system_instruction()

        consulted_marketing = any(
            "ヂャイアン(マーケ):" in line
            or "@ヂャイアン(マーケ)" in line
            or "Gian_Agent:" in line
            or "@Gian_Agent" in line
            for line in history
        )
        consulted_dev = any(
            "スゴ杉くん(エンジニア):" in line
            or "@スゴ杉くん(エンジニア)" in line
            or "Takasugi_Agent:" in line
            or "@Takasugi_Agent" in line
            for line in history
        )
        phase_label = self._phase_label(current_turn, max_turns)
        phase_instruction = self._phase_instruction(current_turn, max_turns)
        remaining_turns = max(max_turns - current_turn + 1, 0)
        final_phase = force_final or current_turn >= max_turns
        kitchen_sink_detected = self._is_kitchen_sink_proposal(history)
        endgame_turn = 8 <= current_turn <= 9
        consensus_likely = self._looks_like_consensus(history)
        tradeoff_compared = self._has_tradeoff_comparison(history)
        can_finish_early = (
            current_turn >= 6 and consulted_marketing and consulted_dev and tradeoff_compared
        )

        dynamic_instruction = (
            f"{base_instruction}\n"
            f"{self.CALL_NAME_RULES}\n"
            "【進行ルール】\n"
            f"1. 現在ターン: {current_turn}/{max_turns}。フェーズ: {phase_label}。{phase_instruction}\n"
            "2. 原則は CALL_AGENT で議論を進める。"
            "Turn 1〜5 では絶対に FINISH_FOR_PRESIDENT を選ばない。\n"
            "3. CALL_AGENT の場合は target_agent と instruction_for_target を必ず設定し、抽象論ではなく具体的な比較質問を出す。\n"
            f"4. 相談状況: マーケ={consulted_marketing}, 開発={consulted_dev}。片側に偏らないように次の担当を選ぶ。\n"
            "5. speech では、前ターンとの差分を明示し、同じ冒頭定型・同じ論点の繰り返しを禁止。\n"
            "6. 既に合意済み/論破済みの主張は蒸し返さず、必要なら『合意済み』と短く参照して次の未解決論点へ進む。\n"
            "7. 『全部盛り』は不可。取捨選択や優先順位は必要だが、毎ターン同じ型で機械的に書かず自然な会話として述べる。\n"
            "8. Turn 1〜4 は発散専用で、毎ターン別案の追加とエンジニアへの問いかけを必須化。"
            "Turn 5〜7 は複数案の衝突/比較。Turn 8〜10 は新規拡張禁止で最終集約。\n"
            f"9. 残りターンは {remaining_turns} 回。残り3回以下では、文脈に馴染む言い方で収束を促してよい（固定文のコピペは禁止）。\n"
            "10. phase フィールドは現在フェーズ名を返し、FINISH_FOR_PRESIDENT 時は final_recommendation / final_category / revision_guidance を必ず埋める。\n"
            "11. speech の可読性を最優先し、壁テキストを避ける。要約や取捨選択は箇条書きと太字を使い、話題ごとに空行を入れる。\n"
            f"12. 早期終了許可条件: current_turn>=6 かつ マーケ/開発の両視点出揃い かつ 複数案の比較・トレードオフ議論済み。判定={can_finish_early}。\n"
            "13. 会議中は社長への途中確認・途中相談をしない。社長への呼びかけは FINISH_FOR_PRESIDENT で最終提出するときだけに限定する。\n"
            "14. 履歴に実発言がないメンバーを、参加済み・賛同済み・返答済みのように扱わない。未発言相手への感謝や応答を捏造しない。\n"
        )
        if current_turn <= 4:
            dynamic_instruction += (
                "15. 発散フェーズ専用: マーケ案を肯定しつつ、必ず『別の切り口』を要求し、"
                "次の担当にスゴ杉くん(エンジニア)を積極的に指名してください。\n"
            )
        if current_turn <= 5:
            dynamic_instruction += (
                "16. このターンは提出禁止。next_action は CALL_AGENT を選択してください。\n"
            )
        if not consulted_dev and current_turn <= 4:
            dynamic_instruction += (
                "17. まだエンジニア視点が未収集です。target_agent は dev を優先し、"
                "実装コスト・技術アイデア・負荷見積りを質問してください。\n"
            )
        if kitchen_sink_detected:
            dynamic_instruction += (
                "18. 直前に全部乗せ傾向があります。二者択一で何を捨てるかを必ず確定し、残す側の見せ方を具体化してください。\n"
            )
        if endgame_turn:
            dynamic_instruction += (
                "19. 終盤ルール: 新規案の追加は禁止。削る候補2つを比較し、どちらを捨てるかを明言してください。\n"
            )
        if force_convergence:
            dynamic_instruction += (
                "20. オーケストレーター判定で膠着中です。比較対象を2案までに圧縮し、"
                "どちらを優先するかを明確にしてください。\n"
            )
        if revision_guidance:
            dynamic_instruction += (
                "21. 社長のNoGo修正方針は最優先制約として、問い・比較・最終提案のすべてに反映してください。\n"
            )
        if consensus_likely and not final_phase and can_finish_early:
            dynamic_instruction += (
                "22. 直近ログに合意の兆候があります。未解決論点がなければこのターンで FINISH_FOR_PRESIDENT を選択してください。\n"
            )
        if final_phase:
            dynamic_instruction += (
                "23. このターンでは FINISH_FOR_PRESIDENT を返し、推奨1案を明確に示してください。\n"
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
                    "enum": ["CALL_AGENT", "FINISH_FOR_PRESIDENT"],
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
            "required": ["speech", "phase", "next_action"],
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "next_action": {"const": "CALL_AGENT"}
                        }
                    },
                    "then": {
                        "required": ["target_agent", "instruction_for_target"]
                    },
                },
                {
                    "if": {
                        "properties": {
                            "next_action": {"const": "FINISH_FOR_PRESIDENT"}
                        }
                    },
                    "then": {
                        "required": ["final_recommendation", "final_category", "revision_guidance"]
                    },
                },
            ],
            "additionalProperties": False,
        }

        response = await self.generate_content_with_retry(
            client=self.client,
            model=self.model_name,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                system_instruction=dynamic_instruction,
                response_mime_type="application/json",
                responseJsonSchema=response_json_schema,
                temperature=0.4,
            ),
            request_name=f"{self.name} decide_next_step",
        )

        response_text = getattr(response, "text", None)
        if not response_text:
            response_text = self.extract_text_from_response(response)

        decision = PMDecision.model_validate_json(response_text)

        if decision.next_action == "FINISH_FOR_PRESIDENT" and current_turn <= 5:
            decision.next_action = "CALL_AGENT"
            decision.target_agent = "dev" if not consulted_dev else "marketing"
            decision.instruction_for_target = (
                "まだ発散/序盤フェーズです。提出は行わず、"
                "別案の追加か、技術面と集客面の不足視点を補ってください。"
            )

        if (
            decision.next_action == "FINISH_FOR_PRESIDENT"
            and current_turn >= 6
            and not can_finish_early
        ):
            decision.next_action = "CALL_AGENT"
            if not consulted_dev:
                decision.target_agent = "dev"
                decision.instruction_for_target = (
                    "提出前チェックです。エンジニア視点が不足しています。"
                    "実装難易度・工数・技術リスクを具体化してください。"
                )
            elif not consulted_marketing:
                decision.target_agent = "marketing"
                decision.instruction_for_target = (
                    "提出前チェックです。マーケ視点が不足しています。"
                    "訴求軸・拡散導線・ターゲット適合性を具体化してください。"
                )
            else:
                decision.target_agent = "dev"
                decision.instruction_for_target = (
                    "提出前チェックです。比較が不足しています。"
                    "候補2案のトレードオフ(実装負荷×拡散性)を明示し、"
                    "採用/不採用理由を短く整理してください。"
                )

        if decision.next_action == "CALL_AGENT" and kitchen_sink_detected:
            fork_prompt = (
                "全部乗せは禁止です。候補A/Bのどちらを捨てるかを決め、"
                "残した側を図形とテキスト中心でどう最もシュールに見せるかを書いてください。"
            )
            decision.instruction_for_target = (
                f"{decision.instruction_for_target}\n{fork_prompt}"
                if decision.instruction_for_target
                else fork_prompt
            )

        if decision.next_action == "CALL_AGENT" and endgame_turn:
            final_trim_instruction = (
                "終盤です。新しい案の追加は禁止。これまでの案から削る候補を2つ挙げ、"
                "どちらを捨てるかを決めて、最終的に残す1案の核だけを報告してください。"
                "残す案は図形・テキスト・軽量アニメの範囲で成立する具体的な見せ方に限定してください。"
            )
            decision.instruction_for_target = (
                f"{decision.instruction_for_target}\n{final_trim_instruction}"
                if decision.instruction_for_target
                else final_trim_instruction
            )

        return decision

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

        response = await self.generate_content_with_retry(
            client=self.client,
            model=self.model_name,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                system_instruction=instruction,
                response_mime_type="text/plain",
                temperature=self.temperature,
            ),
            request_name=f"{self.name} generate_spec_for_plan",
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

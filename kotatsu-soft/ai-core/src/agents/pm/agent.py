import asyncio
from datetime import datetime
from pathlib import Path
import re
from typing import Optional
from google import genai
from google.genai import types

from agents.base_agent import BaseAgent
from agents.pm.schemas import PMDecision


class PMAgent(BaseAgent):
    _DEADLINE_REMINDER_RE = re.compile(
        r"^\s*(?:[-・*]\s*)?議論できるのはあと\s*\d+\s*回よ。そろそろ絞り込みましょう\s*\n*"
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
        if current_turn <= 8:
            return "CONFLICT"
        return "FINAL"

    def _phase_instruction(self, current_turn: int, max_turns: int) -> str:
        phase = self._phase_label(current_turn, max_turns)
        if phase == "DIVERGENCE":
            return (
                "このフェーズでは、まずは発散を最優先してください。"
                "注目軸は『新奇性』『笑い・シュールさ』『世界観の尖り』です。"
                "案を広げる際は、単に数を増やすのではなく、どこが新しいのか、どこが笑えるのか、どこで世界観が立つのかを見極め、"
                "足りない案には別角度の反証や追加視点を求めてください。"
            )
        if phase == "CONFLICT":
            if 8 <= current_turn <= 9:
                return (
                    "【重要・終盤フェーズ】新しい大風呂敷を広げることを禁止します。"
                    "この時点では、これまで出た案のどれを削るかに集中し、"
                    "必ず二者択一で『どちらを捨てるか』を明言してください。"
                    "追加アイデアの提案ではなく、残す要素と捨てる要素の最終調整だけを行ってください。"
                )
            return (
                "【重要・衝突フェーズ】このフェーズでは、PM自身が審判として鋭くツッコミを入れてください。"
                "注目軸は『工数・バグリスク（デバッグ地獄）』と『表現の過激さ vs 動作の安定性』です。"
                "マーケの大げさな案には、どこで壊れるか、どこが実装負債になるか、何を削れば成立するかを突き、"
                "開発の安全すぎる案には、攻めの表現をどこまで許容できるか、何を残せば尖るかを問い、"
                "トレードオフを明示しながら最も魅力的な1点へ絞り込んでください。"
            )
        return (
            "この最終フェーズでは、比較のための雑な横並びではなく、"
            "1案だけを推す形でまとめてください。"
            "注目軸は『初見のインパクト』『SNS拡散性』『リトライのテンポ』です。"
            "初見で何が刺さるのか、共有したくなる理由は何か、もう一回遊びたくなるテンポはあるかを中心に整理してください。"
            "final_recommendation には社長向けの1案の要約を、"
            "final_category にはその案のカテゴリ名を、"
            "revision_guidance には NoGo のときに直すべき方向を入れてください。"
            "最後に、社長に渡すために FINISH_FOR_PRESIDENT を返してください。"
        )

    def _remaining_turns(self, current_turn: int, max_turns: int) -> int:
        return max(max_turns - current_turn + 1, 0)

    def _deadline_reminder(self, current_turn: int, max_turns: int) -> str:
        remaining = self._remaining_turns(current_turn, max_turns)
        return f"議論できるのはあと{remaining}回よ。そろそろ絞り込みましょう"

    def _strip_leading_deadline_reminders(self, text: Optional[str]) -> str:
        cleaned = (text or "").lstrip()
        while True:
            match = self._DEADLINE_REMINDER_RE.match(cleaned)
            if not match:
                break
            cleaned = cleaned[match.end():].lstrip()
        return cleaned

    def _prepend_deadline_if_needed(self, text: Optional[str], current_turn: int, max_turns: int) -> str:
        body = self._strip_leading_deadline_reminders(text)
        if current_turn < 5:
            return body

        reminder = self._deadline_reminder(current_turn, max_turns)
        if not body:
            return reminder
        return f"{reminder}\n{body}"

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

    @staticmethod
    def _hybrid_bridge_instruction(target_role: Optional[str], current_turn: int) -> str:
        base = (
            "今回は単なる却下や二者択一だけで終わらせず、"
            "ヂャイアンのバカバカしい世界観をスゴ杉くんの『図形とテキストだけの最速レシピ』で成立させる"
            "ハイブリッド案を必ず1つ入れてください。"
            "派手なアセット追加ではなく、軽量な実装トリックでシュールさを増幅する前提で考えてください。"
        )
        if target_role == "marketing":
            role_specific = (
                "マーケ視点では、チープな画面だからこそ刺さる巨大ワード、理不尽な見せ方、"
                "SNSで切り取りたくなる一瞬を定義し、エンジニアが即実装できる粒度に落としてください。"
            )
        elif target_role == "dev":
            role_specific = (
                "開発視点では、Canvasの文字ポップアップ、一定フレームごとの点滅、単純な図形移動のような"
                "軽量トリックを最低1つ示し、その世界観をどう最速で再現するか具体化してください。"
            )
        else:
            role_specific = (
                "世界観の尖りと実装の軽さを両立させるため、残す演出1つと削る演出1つも明記してください。"
            )

        convergence = (
            "残す演出と捨てる演出を1つずつ明記し、なぜその折衷が一番シュールで実装しやすいかを答えてください。"
        )
        if current_turn >= 8:
            convergence = (
                "終盤なので新要素の追加は禁止です。既に出た案の部品だけを使って、"
                "最終候補として残す核1つと捨てる要素1つを確定してください。"
            )

        return f"{base}{role_specific}{convergence}"

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
        final_phase = force_final or current_turn >= max_turns
        kitchen_sink_detected = self._is_kitchen_sink_proposal(history)
        endgame_turn = 8 <= current_turn <= 9

        dynamic_instruction = (
            f"{base_instruction}\n"
            "【現在の議論状況と判定条件】\n"
            f"1. 現在のターン数: {current_turn} / 最大 {max_turns} ターン。\n"
            f"2. 現在の会議フェーズ: {phase_label}。{phase_instruction}\n"
            "3. まずは必ず ヂャイアン(マーケ) と スゴ杉くん(エンジニア) の両方に、違う見方で意見を出させてください。\n"
            "4. current_turn が max_turns より小さい場合、next_action は必ず 'CALL_AGENT' とし、FINISH_FOR_PRESIDENT を選ばないでください。\n"
            "5. current_turn が max_turns の場合のみ、next_action は 'FINISH_FOR_PRESIDENT' としてください。\n"
            "6. CALL_AGENT の場合、target_agent と instruction_for_target を必ず記載し、次の専門家に向けた具体的な問いを作成してください。\n"
            "7. speech では、今の進み具合を短くまとめつつ、フェーズに応じた鋭いツッコミ、リスク指摘、または矛盾指摘を少なくとも1つ入れてください。固定の3質問を毎回並べる必要はなく、phase_instruction の評価軸に沿って変化させてください。\n"
            "8. speech は毎回 500文字前後（目安 400〜600文字）に収めてください。\n"
            "9. Turn 1〜4では、たくさんのアイデアを出すように促してください。\n"
            "10. Turn 5〜8では、作れるかどうかと面白いかどうかをぶつけて、残す案と捨てる案をはっきりさせてください。\n"
            "11. Turn 9〜10では最終案を1案に絞る準備を行い、Turn 10で必ず FINISH_FOR_PRESIDENT を返してください。\n"
            f"12. 現在の相談状況: ヂャイアン(マーケ) に相談済み={consulted_marketing}, スゴ杉くん(エンジニア) に相談済み={consulted_dev}。\n"
            "13. divergence_prompt には、さらに別の発想を広げる短い質問を入れてください。\n"
            "14. next_action が 'FINISH_FOR_PRESIDENT' の場合、JSON に final_recommendation / final_category / revision_guidance を含めてください。\n"
            "15. final_recommendation は推奨する1案の要約を、final_category はその案のカテゴリ名、revision_guidance は NoGo 時の修正方針を返してください。\n"
            "16. phase フィールドに現在のフェーズ名を返してください。\n"
            "17. メンバーの意見を受けるとき、単なる称賛（例: 素敵、ありがとう）だけで終えることを禁止し、必ず検証質問か反証を続けてください。\n"
            "18. 毎ターン、現在フェーズの評価軸のうち特に重要な観点を1つ以上要約内で触れてください。DIVERGENCE では新奇性・笑い・世界観の尖り、CONFLICT では工数・バグリスクと安定性のトレードオフ、FINAL では初見のインパクト・SNS拡散性・リトライのテンポを優先してください。\n"
            "19. 『全部盛り込み』の妥協は禁止。speech または instruction_for_target に、残す要素と捨てる要素を必ず明記してください。\n"
            "20. トレードオフ（あれを立てればこれが立たず）を毎ターン最低1つ示し、どちらを優先するかを明言してください。\n"
            "21. Turn 5以降の残りターン数リマインドはシステム側で自動付与します。"
            "あなた自身が同じ定型文を冒頭に繰り返し書くことを禁止します。\n"
            "22. speech は長文のベタ書きを避け、2〜4文ごとに改行し、要点は箇条書き（・または1. 2.）で整理してください。\n"
            "23. 評価軸を列挙するときは、必要な場合に限って箇条書きを使い、固定の3点セットに縛られずフェーズに合う鋭い切り口を優先してください。\n"
            "24. Turn 8〜9では、新規アイデアの追加・拡張を禁止し、これまでの案のうち何を削るかの最終調整だけを行ってください。\n"
            "25. CONFLICTフェーズでは、単にマーケ案を削るか開発案を守るかで終わらず、"
            "『世界観の尖りを保ったまま、図形・文字・軽量アニメだけで最もシュールに見せる折衷案』を最低1つ作らせてください。\n"
            "26. CALL_AGENT の instruction_for_target では、次の発言者に対して、残す演出1つ・捨てる演出1つ・軽量実装トリック1つを必ず求めてください。\n"
        )
        if kitchen_sink_detected:
            dynamic_instruction += (
                "27. 直前のマーケ提案は『全部乗せ』傾向です。曖昧に受け流すことを禁止します。"
                "PMは必ず『あれもこれもは1日で作れない。〇〇と××のどちらか一つを今すぐ捨てなさい』"
                "という二者択一を明言しつつ、捨てない側を図形・文字中心でどう一番面白く見せるかの折衷案も同時に要求してください。\n"
            )
        if endgame_turn:
            dynamic_instruction += (
                "28. 終盤ルール: CALL_AGENTする場合も、指示は必ず『削る候補2つを提示し、どちらを捨てるか決める』形式にしてください。"
                "新機能・新演出・新ジャンルの追加提案を求めてはいけません。"
                "ただし、残した1案を図形・文字・軽量演出だけで成立させる最終ハイブリッド案の明文化は必須です。\n"
            )
        if force_convergence:
            dynamic_instruction += (
                "29. オーケストレーター判定で足踏み状態です。次の一手は強制収束モードで進め、"
                "比較対象を最大2案に圧縮し、必ず1つを捨てる決定を宣言してください。"
                "そのうえで、残した案を制約下でどう最も映えさせるかのハイブリッド解を具体化してください。\n"
            )
        if revision_guidance:
            dynamic_instruction += (
                "30. 社長のNoGo修正方針が与えられている場合、"
                "その内容を最優先制約として扱い、"
                "次の問いかけ・比較観点・最終提案の全てに必ず反映してください。\n"
                "31. FINISH_FOR_PRESIDENT の際は、final_recommendation 内で"
                "修正方針をどう満たしたかを明記してください。\n"
            )
        if final_phase:
            dynamic_instruction += (
                "32. このターンでは必ず FINISH_FOR_PRESIDENT を返し、"
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

        if decision.next_action == "CALL_AGENT":
            hybrid_instruction = self._hybrid_bridge_instruction(
                decision.target_agent,
                current_turn,
            )
            decision.instruction_for_target = (
                f"{decision.instruction_for_target}\n{hybrid_instruction}"
                if decision.instruction_for_target
                else hybrid_instruction
            )

        if current_turn >= 5:
            decision.speech = self._prepend_deadline_if_needed(
                decision.speech,
                current_turn,
                max_turns,
            )
            if decision.next_action == "CALL_AGENT":
                decision.instruction_for_target = self._prepend_deadline_if_needed(
                    decision.instruction_for_target,
                    current_turn,
                    max_turns,
                )

        if decision.next_action == "CALL_AGENT" and kitchen_sink_detected:
            fork_prompt = (
                "あれもこれもは1日で作れないから、候補Aと候補Bのどちらか一つを今すぐ捨てなさい。"
                "捨てる理由を工数と面白さの両面で1つずつ示してください。"
                "そのうえで、残した側を図形とテキスト中心でどう一番シュールに見せるかも必ず書いてください。"
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

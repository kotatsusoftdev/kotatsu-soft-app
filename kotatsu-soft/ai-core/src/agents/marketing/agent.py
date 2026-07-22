import asyncio
from google import genai
from google.genai import types
from agents.base_agent import BaseAgent


class MarketingAgent(BaseAgent):
    CALL_NAME_RULES = (
        "【呼称ルール】\n"
        "- 一人称は『オレ』または『オレ様』を使う。\n"
        "- すずかちゃん（PM）への呼び方は『すずか』で統一する。\n"
        "- スゴ杉くん（エンジニア）への呼び方は『スゴ杉』で統一する。\n"
        "- 社長（ユーザー）への呼び方は『のぶ太』を使う。\n"
        "- メンバーに意見を振る・返答する際は、本文中やメンション（@呼び名）で自然に相手の名前を呼ぶ。\n"
        "- 上記以外の呼び名（あだ名・省略名）は使わない。\n"
    )

    def __init__(self, api_key: str, config_path: str, mention_id: str):
        super().__init__(
            config_path=config_path,
            avatar_url=(
                "https://raw.githubusercontent.com/kotatsusoftdev/kotatsu-soft-app/main/kotatsu-soft/ai-core/assets/avatars/marketing.png"
            ),
            mention_id=mention_id,
        )
        self.client = genai.Client(api_key=api_key)

    @staticmethod
    def _has_spoken(history: list[str], markers: tuple[str, ...]) -> bool:
        return any(any(marker in line for marker in markers) for line in history)

    async def think_and_reply(self, prompt: str, conversation_history: list[str]) -> str:
        dev_has_spoken = self._has_spoken(
            conversation_history,
            ("スゴ杉くん(エンジニア):", "Takasugi_Agent:", "dev:"),
        )
        system_instruction = (
            f"{self.build_system_instruction()}\n"
            f"{self.CALL_NAME_RULES}\n"
            "【会議中の会話制約】\n"
            "- 途中提出や途中確認は禁止。会議中は社長への確認・呼びかけ・『これでいいか』のような問いかけを行わない。\n"
            "- 履歴に実際の発言がない相手へ、お礼・返答・賛同・反論をしたことにしない。未発言メンバーを会話に参加済みのように扱わない。\n"
            f"- スゴ杉さんの発言履歴あり={dev_has_spoken}。False の間はスゴ杉さんに話しかけたり、スゴ杉さんの案を受けたような言い方をしない。\n"
        )
        prompt_text = (
            f"{prompt}\n\n"
            "これまでの議論履歴:\n"
            + ("\n".join(conversation_history) if conversation_history else "（議論未開始）")
        )

        response = await self.generate_content_with_retry(
            client=self.client,
            model=self.model_name,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="text/plain",
                temperature=self.temperature,
            ),
            request_name=f"{self.name} think_and_reply",
        )

        return self.extract_text_from_response(response)

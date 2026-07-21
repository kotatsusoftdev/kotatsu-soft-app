import asyncio
from google import genai
from google.genai import types
from agents.base_agent import BaseAgent


class DevAgent(BaseAgent):
    def __init__(self, api_key: str, config_path: str, mention_id: str):
        super().__init__(
            config_path=config_path,
            avatar_url=(
                "https://raw.githubusercontent.com/kotatsusoftdev/kotatsu-soft-app/main/kotatsu-soft/ai-core/assets/avatars/dev.png"
            ),
            mention_id=mention_id,
        )
        self.client = genai.Client(api_key=api_key)

    async def think_and_reply(self, prompt: str, conversation_history: list[str]) -> str:
        system_instruction = self.build_system_instruction()
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

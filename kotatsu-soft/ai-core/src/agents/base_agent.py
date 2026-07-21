import yaml
from abc import ABC, abstractmethod
from typing import Any


class BaseAgent(ABC):
    def __init__(self, config_path: str, avatar_url: str, mention_id: str):
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        agent_config = self.config.get("agent", {})
        if not agent_config:
            raise ValueError("agent config is missing in YAML file")

        self.name = agent_config["name"]
        self.role = agent_config["role"]
        self.title = agent_config["title"]
        self.avatar_url = avatar_url
        self.mention_id = mention_id

        llm_config = agent_config.get("llm") or self.config.get("llm")
        if not llm_config:
            raise ValueError("LLM config is missing in YAML file")

        self.temperature = llm_config["temperature"]
        self.model_name = llm_config["model"]

    def build_system_instruction(self) -> str:
        agent_config = self.config.get("agent", {})
        criteria = agent_config.get("evaluation_criteria") or self.config.get("evaluation_criteria")
        if not criteria:
            raise ValueError("evaluation_criteria is missing in agent config")

        output_format = (
            criteria.get("output_format")
            or agent_config.get("output_format")
            or self.config.get("output_format")
        )
        if not output_format:
            raise ValueError("output_format is missing in agent config")

        rules = "\n- ".join(criteria["decision_rules"])
        return (
            f"あなたはシステム内の自律型エージェント「{self.name}（{self.title}）」です。\n"
            "論理的かつ客観的な判断を優先しつつ、やや会話調で読みやすい表現を使ってください。\n"
            "工数見積もりは行わず、実装の手軽さと実現性に集中してください。\n\n"
            "各回答は 500文字前後（目安 400〜600文字）に収め、冗長な前置きは避けてください。\n\n"
            f"必要に応じて他エージェントを指名する際は、{self.mention_id} を含むメンション形式を利用してください。\n\n"
            "【最優先評価軸】\n"
            f"- {criteria['primary_focus']}\n\n"
            "【行動ルール】\n"
            f"- {rules}\n\n"
            "【出力形式】\n"
            f"- {output_format}\n"
        )

    @staticmethod
    def extract_text_from_response(response: Any) -> str:
        if hasattr(response, "text") and response.text:
            return response.text

        candidates = getattr(response, "candidates", None)
        if candidates:
            first_candidate = candidates[0]
            content = getattr(first_candidate, "content", None)
            if content is not None:
                if hasattr(content, "text") and content.text:
                    return content.text
                parts = getattr(content, "parts", None)
                if parts:
                    return "".join(
                        part.text if hasattr(part, "text") else part.get("text", "")
                        for part in parts
                        if part
                    )

        chunks: list[str] = []
        for output in getattr(response, "output", []) or []:
            for content in getattr(output, "content", []) or []:
                if hasattr(content, "text") and content.text:
                    chunks.append(content.text)
                elif isinstance(content, dict) and content.get("text"):
                    chunks.append(content["text"])

        return "".join(chunks).strip()

    @abstractmethod
    async def think_and_reply(self, prompt: str, conversation_history: list[str]) -> str:
        raise NotImplementedError()

from __future__ import annotations

from typing import Any

import requests

from app.config import load_config


class OpenAIResponsesTransport:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_sec: int | None = None,
        base_url: str = "https://api.openai.com/v1/responses",
    ):
        cfg = load_config()
        self.api_key = str(api_key if api_key is not None else cfg.openai_api_key).strip()
        self.model = str(model if model is not None else cfg.openai_advisor_model).strip()
        self.timeout_sec = int(timeout_sec if timeout_sec is not None else cfg.openai_advisor_timeout_sec)
        self.base_url = str(base_url).strip()

    def __call__(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        if not self.model:
            raise RuntimeError("OPENAI_ADVISOR_MODEL is not configured")
        payload = {
            "model": self.model,
            "input": prompt,
        }
        response = requests.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        data = response.json()
        return self._extract_text(data)

    @staticmethod
    def _extract_text(data: Any) -> str:
        if not isinstance(data, dict):
            raise RuntimeError("OpenAI response is not a JSON object")
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text
        chunks: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        text = "".join(chunks).strip()
        if text:
            return text
        raise RuntimeError("OpenAI response did not include text output")

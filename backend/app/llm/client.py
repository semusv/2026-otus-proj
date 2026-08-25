"""Асинхронный клиент LLM (OpenAI-совместимый API).

Работает с LM Studio (dev) и vLLM (целевой движок) без изменений кода -
переключение только через APP_LLM_BASE_URL/APP_LLM_MODEL (ADR-001).
"""

import json
import re
from dataclasses import dataclass

from openai import AsyncOpenAI


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str


class LLMClient:
    """Тонкая обёртка над chat.completions c устойчивым парсингом JSON-ответов."""

    def __init__(self, config: LLMConfig, *, timeout_s: float = 120.0) -> None:
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key or "lm-studio",
            timeout=timeout_s,
        )
        self.model = config.model

    async def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> str:
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ""
        return content.strip()

    async def complete_json(self, system: str, user: str, **kwargs: float) -> object:
        """complete + извлечение JSON из ответа (модели любят добавлять prose/```json)."""
        raw = await self.complete(system, user, **kwargs)  # type: ignore[arg-type]
        return parse_loose_json(raw)


_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def parse_loose_json(raw: str) -> object | None:
    """Достаёт JSON из ответа модели: чистый JSON, ```json-блок или первый [...] объект."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_ARRAY_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None

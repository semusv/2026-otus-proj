"""Асинхронный клиент LLM (OpenAI-совместимый API).

Работает с LM Studio (dev) и vLLM (целевой движок) без изменений кода -
переключение только через APP_LLM_BASE_URL/APP_LLM_MODEL (ADR-001).
Каждый вызов отражается generation'ой в Langfuse (этап 7, best-effort).
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openai import APIError, AsyncOpenAI

from app.observability.langfuse_client import finish_generation, llm_generation

_RETRY_DELAY_S = 0.5


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str


class LLMClient:
    """Тонкая обёртка над chat.completions c устойчивым парсингом JSON-ответов.

    Транзиентные ошибки upstream (LM Studio/vLLM) ретраятся ОДИН раз -
    наблюдаемый плавающий сбой jinja-рендера промпта лечится повтором.
    """

    def __init__(self, config: LLMConfig, *, timeout_s: float = 120.0) -> None:
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key or "lm-studio",
            timeout=timeout_s,
        )
        self.model = config.model

    async def _create_with_retry(self, **kwargs: Any) -> Any:
        try:
            return await self._client.chat.completions.create(**kwargs)
        except APIError:
            await asyncio.sleep(_RETRY_DELAY_S)
            return await self._client.chat.completions.create(**kwargs)

    async def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        with llm_generation(name="llm.complete", model=self.model,
                                  messages=messages) as gen:
            try:
                response = await self._create_with_retry(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                result = (response.choices[0].message.content or "").strip()
            except Exception as exc:
                finish_generation(gen, output="", error=str(exc)[:300])
                raise
            finish_generation(gen, output=result)
            return result

    async def complete_json(self, system: str, user: str, **kwargs: float) -> object | None:
        """complete + извлечение JSON из ответа (модели любят добавлять prose/```json)."""
        raw = await self.complete(system, user, **kwargs)  # type: ignore[arg-type]
        return parse_loose_json(raw)

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """Стриминг токенов генерации (SSE-эндпоинт чата, этап 5)."""
        with llm_generation(name="llm.stream", model=self.model,
                                  messages=messages) as gen:
            accumulated: list[str] = []
            try:
                response = await self._create_with_retry(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                async for chunk in response:
                    if chunk.choices:
                        delta = chunk.choices[0].delta.content
                        if delta:
                            accumulated.append(delta)
                            yield delta
            except Exception as exc:
                finish_generation(gen, output="".join(accumulated),
                                  error=str(exc)[:300])
                raise
            finish_generation(gen, output="".join(accumulated))


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

"""LLM-as-a-Judge промпт-тесты (gpu_slow, запуск вручную: make test-judge).

Проверяют контракты ПРОМПТОВ на реальной модели (LM Studio/vLLM, ADR-001/002):
генерация с цитатами [S*], evaluate-верdict, re-plan JSON, стриминг.
Compose не нужен; LM Studio должен быть запущен - иначе тесты skip.
"""

from collections.abc import AsyncIterator

import pytest
from app.agents.guardrails import ContextSource, build_context_block, extract_citations
from app.agents.graph import EVALUATE_SYSTEM, GENERATION_SYSTEM
from app.agents.planner import REPLAN_SYSTEM
from app.llm.client import LLMClient, LLMConfig
from app.rag.tools import KNOWN_TOOLS

pytestmark = pytest.mark.gpu_slow

SOURCES = [
    ContextSource(
        source_id="S1",
        act_id="100",
        title="Об акционерных обществах",
        chunk_no=0,
        clearance="PUBLIC",
        text=(
            "Статья 1. Акционерное общество обязано раскрывать годовой отчёт, "
            "бухгалтерскую отчётность и иную информацию в соответствии с законом."
        ),
    ),
    ContextSource(
        source_id="S2",
        act_id="200",
        title="О порядке публикации",
        chunk_no=1,
        clearance="PUBLIC",
        text="Статья 2. Публикация осуществляется в сети Интернет на официальной странице.",
    ),
]


@pytest.fixture(scope="module")
async def llm(judge_settings) -> AsyncIterator[LLMClient]:
    client = LLMClient(
        LLMConfig(
            base_url=judge_settings.llm_base_url,
            api_key=judge_settings.llm_api_key.get_secret_value(),
            model=judge_settings.llm_model,
        ),
        timeout_s=90,
    )
    try:
        await client.complete("Ты - эхо-бот. Ответь словом: понято", "понято?", max_tokens=8)
    except Exception as exc:
        pytest.skip(f"LLM недоступен по {judge_settings.llm_base_url}: {exc}")
    yield client


async def _generate(llm: LLMClient, question: str) -> str:
    context = build_context_block(SOURCES, "")
    messages = [
        {"role": "system", "content": GENERATION_SYSTEM},
        {"role": "user", "content": f"Источники:\n{context}\n\nВопрос пользователя: {question}"},
    ]
    parts: list[str] = []
    async for delta in llm.stream(messages, max_tokens=512):
        parts.append(delta)
    return "".join(parts)


class TestGenerationPrompt:
    async def test_answer_contains_valid_citations(self, llm: LLMClient) -> None:
        answer = await _generate(llm, "Что обязано раскрывать акционерное общество?")
        assert answer.strip(), "модель вернула пустой ответ"
        checked = extract_citations(answer, SOURCES)
        assert checked.citations, f"нет цитат в ответе: {answer[:300]}"
        assert checked.dropped_citations == [], (
            f"модель выдумала источники {checked.dropped_citations}: {answer[:300]}"
        )

    async def test_judge_confirms_groundedness(self, llm: LLMClient) -> None:
        answer = await _generate(llm, "Где публикуется информация акционерного общества?")
        judge_prompt = (
            "Проверь ответ ассистента против источников. Ответ корректен, если каждый "
            "факт подтверждается источниками и ссылки [S*] использованы уместно.\n\n"
            f"Источники:\n{build_context_block(SOURCES, '')}\n\nОтвет ассистента:\n{answer}"
        )
        verdict = await llm.complete_json(
            "Ты - строгий судья качества RAG-ответов. Ответь СТРОГО JSON: "
            '{"grounded": true|false, "reason": "кратко"}',
            judge_prompt,
            max_tokens=96,
        )
        assert isinstance(verdict, dict), f"не-JSON вердикт: {verdict!r}"
        assert verdict.get("grounded") is True, f"судья отклонил ответ: {verdict}"


class TestServicePrompts:
    async def test_evaluate_prompt_returns_contract(self, llm: LLMClient) -> None:
        payload = (
            '{"question": "Что обязано раскрывать АО?", '
            '"found_sources": ["[S1] Об акционерных обществах"], '
            '"answer_preview": "АО обязано раскрывать годовой отчёт [S1]."}'
        )
        verdict = await llm.complete_json(EVALUATE_SYSTEM, payload, max_tokens=128)
        assert isinstance(verdict, dict)
        assert isinstance(verdict.get("sufficient"), bool), f"контракт нарушен: {verdict!r}"

    async def test_replan_prompt_returns_tools_and_query(self, llm: LLMClient) -> None:
        user_payload = (
            '{"question": "ответственность за непубликацию", '
            '"previous_tools": ["retrieve_vec", "expand_graph"], '
            '"retrieved": "векторные хиты: Об АО", '
            '"evaluator_feedback": "нет норм об ответственности"}'
        )
        plan = await llm.complete_json(REPLAN_SYSTEM, user_payload, max_tokens=192)
        assert isinstance(plan, dict), f"не-JSON план: {plan!r}"
        tools = plan.get("tools")
        assert isinstance(tools, list) and tools, f"инструменты не выбраны: {plan!r}"
        assert set(tools) <= set(KNOWN_TOOLS), f"неизвестные инструменты: {tools}"
        query = plan.get("query")
        assert isinstance(query, str) and query.strip(), f"пустой запрос: {plan!r}"

    async def test_streaming_yields_incremental_deltas(self, llm: LLMClient) -> None:
        deltas: list[str] = []
        async for delta in llm.stream(
            [
                {"role": "system", "content": "Отвечай одним коротким предложением."},
                {"role": "user", "content": "Что такое акционерное общество?"},
            ],
            max_tokens=128,
        ):
            deltas.append(delta)
        assert len(deltas) > 1, "стриминг отдаёт несколько дельт"
        assert "".join(deltas).strip()

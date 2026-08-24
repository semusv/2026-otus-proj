# ADR-001: Движок LLM Serving

- Статус: принято
- Дата: 2026-08-25
- Связанные: ADR-002 (модель), ADR-010 (deployment)

## Контекст

Нужен self-hosted инференс LLM в закрытом контуре (внешние API запрещены заданием).
Железо: NVIDIA RTX 5070 Ti, 16GB VRAM, архитектура Blackwell (compute capability **sm_120**) —
требует CUDA ≥ 12.8 и свежих сборок движков. Windows + Docker Desktop (WSL2, GPU passthrough).

## Решение

**vLLM** (образ `vllm/vllm-openai`, тег с CUDA ≥ 12.8), OpenAI-compatible API, квантование AWQ.

## Trade-offs

| Критерий | vLLM | SGLang | TGI |
|----------|------|--------|-----|
| Throughput (batch) | Высокий (PagedAttention) | Высокий (RadixAttention, лучше на повторных префиксах) | Средний |
| Поддержка новых GPU (Blackwell/sm_120) | Быстрая — релизы с актуальным CUDA | Отстаёт от vLLM | Медленнее всех |
| OpenAI-compatible API | Да, нативно | Да | Да |
| AWQ/GPTQ квантование | Да (awq_marlin kernel) | Да | Ограниченно |
| Экосистема/документация | Лучшая для self-host | Растёт | Хорошая, но проект замедлился |
| Сложность эксплуатации | Низкая (один контейнер) | Низкая | Средняя |

Ключевой фактор выбора: **поддержка sm_120**. vLLM первым добавил сборки под Blackwell;
TGI/SGLang на момент старта проекта требуют ручной сборки или не работают с RTX 50xx.

## Последствия

- Положительные: max throughput на единственной consumer-GPU; совместимый с OpenAI SDK API
  упрощает клиент (`openai` python sdk, base_url из конфига); зрелая поддержка AWQ.
- Отрицательные / риски:
  - образ vLLM тяжёлый (~10–15GB), нужен доступ к Docker Hub (см. ADR-011);
  - KV-cache ограничивает `max_model_len` — фиксируем ~8192 токенов (см. ADR-002);
  - версия образа фиксируется в compose (воспроизводимость), обновление осознанное.

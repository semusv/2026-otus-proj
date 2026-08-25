"""Micro-bench tokens/sec LLM-движка (этап 9, docs/load-report.md).

Прямые вызовы OpenAI-совместимого API (LM Studio на хосте / vLLM) с подсчётом
usage.completion_tokens: API чата бэкенда usage не отдаёт, поэтому токены/сек
serving-движка меряются отдельно от RAG-конвейера.

Запуск: make bench-llm  (или uv run --project backend python scripts/load_test/bench_llm.py)
Параметры - env: BENCH_BASE_URL, BENCH_MODEL, BENCH_SEQ_ROUNDS, BENCH_PAR_WORKERS,
BENCH_PAR_ROUNDS, BENCH_MAX_TOKENS.
"""

import asyncio
import json
import os
import time

from openai import AsyncOpenAI

BASE_URL = os.environ.get("BENCH_BASE_URL", "http://127.0.0.1:1234/v1")
MODEL = os.environ.get("BENCH_MODEL", "qwen3.5-2b")
SEQ_ROUNDS = int(os.environ.get("BENCH_SEQ_ROUNDS", "12"))
PAR_WORKERS = int(os.environ.get("BENCH_PAR_WORKERS", "4"))
PAR_ROUNDS = int(os.environ.get("BENCH_PAR_ROUNDS", "8"))
MAX_TOKENS = int(os.environ.get("BENCH_MAX_TOKENS", "256"))
TIMEOUT_S = 120.0

PROMPT = (
    "Перечисли кратко три основных этапа развития права Древнего Рима. "
    "Ответь тремя короткими абзацами без вступления."
)


async def one_call(client: AsyncOpenAI, sem: asyncio.Semaphore | None) -> dict[str, float]:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0.2,
        "max_tokens": MAX_TOKENS,
    }
    async with sem if sem else _NullCtx():
        t0 = time.perf_counter()
        resp = await client.chat.completions.create(**payload, timeout=TIMEOUT_S)
        dt = time.perf_counter() - t0
    completion_tokens = int(getattr(resp.usage, "completion_tokens", 0) or 0)
    return {
        "seconds": dt,
        "completion_tokens": completion_tokens,
        "tok_per_s": completion_tokens / dt if dt > 0 and completion_tokens else 0.0,
    }


class _NullCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(p * (len(s) - 1))))
    return s[idx]


async def main() -> None:
    client = AsyncOpenAI(base_url=BASE_URL, api_key=os.environ.get("BENCH_API_KEY", "lm-studio"))
    print(f"== bench_llm: {BASE_URL} model={MODEL}")
    print(f"   seq_rounds={SEQ_ROUNDS} par_workers={PAR_WORKERS} par_rounds={PAR_ROUNDS} "
          f"max_tokens={MAX_TOKENS}")

    # прогрев (загрузка модели в память движка, не считается)
    await one_call(client, None)
    print("   warm-up done")

    seq: list[dict[str, float]] = []
    for _ in range(SEQ_ROUNDS):
        seq.append(await one_call(client, None))

    seq_times = [r["seconds"] for r in seq]
    seq_tps = [r["tok_per_s"] for r in seq]
    total_tok = sum(r["completion_tokens"] for r in seq)

    print("\n=== SEQUENTIAL (один запрос за раз) ===")
    print(f"requests: {len(seq)}")
    print(f"latency p50/p95: {pct(seq_times, .5):.2f}s / {pct(seq_times, .95):.2f}s")
    print(f"tokens/request mean: {total_tok / len(seq):.0f}")
    print(f"tokens/sec per-request p50/p95: {pct(seq_tps, .5):.1f} / {pct(seq_tps, .95):.1f}")
    wall_seq = sum(seq_times)
    print(f"aggregate throughput: {sum(r['completion_tokens'] for r in seq) / wall_seq:.1f} tok/s")

    sem = asyncio.Semaphore(PAR_WORKERS)
    t0 = time.perf_counter()
    par = await asyncio.gather(*[one_call(client, sem) for _ in range(PAR_ROUNDS)])
    wall_par = time.perf_counter() - t0

    par_times = [r["seconds"] for r in par]
    par_tps = [r["tok_per_s"] for r in par]

    print(f"\n=== PARALLEL (workers={PAR_WORKERS}, requests={PAR_ROUNDS}) ===")
    print(f"wall time: {wall_par:.2f}s")
    print(f"latency p50/p95: {pct(par_times, .5):.2f}s / {pct(par_times, .95):.2f}s")
    print(f"tokens/sec per-request p50: {pct(par_tps, .5):.1f}")
    print(f"aggregate throughput: {sum(r['completion_tokens'] for r in par) / wall_par:.1f} tok/s")

    summary = {
        "base_url": BASE_URL,
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "sequential": {
            "requests": len(seq),
            "latency_p50_s": round(pct(seq_times, .5), 3),
            "latency_p95_s": round(pct(seq_times, .95), 3),
            "tokens_per_req_mean": round(total_tok / len(seq), 1),
            "tps_p50": round(pct(seq_tps, .5), 2),
            "tps_p95": round(pct(seq_tps, .95), 2),
            "aggregate_tps": round(sum(r["completion_tokens"] for r in seq) / wall_seq, 2),
        },
        f"parallel_{PAR_WORKERS}w": {
            "requests": len(par),
            "wall_s": round(wall_par, 3),
            "latency_p50_s": round(pct(par_times, .5), 3),
            "latency_p95_s": round(pct(par_times, .95), 3),
            "tps_p50": round(pct(par_tps, .5), 2),
            "aggregate_tps": round(sum(r["completion_tokens"] for r in par) / wall_par, 2),
        },
    }
    out = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"bench-llm-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(f"\nsaved: {path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

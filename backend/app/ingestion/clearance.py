"""Детерминированная разметка актов метками доступа для демонстрации RBAC.

Hash от act_id (md5 - стабилен между процессами, в отличие от встроенного hash())
раскладывает акты по корзинам INTERNAL/SECRET с настраиваемыми процентами;
остаток получает PUBLIC. Один и тот же акт всегда получает одну и ту же метку -
воспроизводимость между прогонами ingestion и тестами.
"""

import hashlib
from typing import Literal

Clearance = Literal["PUBLIC", "INTERNAL", "SECRET"]


def resolve_clearance(
    act_id: str, *, internal_percent: int, secret_percent: int
) -> Clearance:
    """Стабильно сопоставляет акту метку доступа по процентам из конфига.

    S324: md5 используется не как криптография, а как стабильный хэш-функционал
    для равномерного распределения по корзинам.
    """
    bucket = int.from_bytes(hashlib.md5(act_id.encode("utf-8")).digest()[:4], "big")  # noqa: S324
    bucket %= 100
    if bucket < secret_percent:
        return "SECRET"
    if bucket < secret_percent + internal_percent:
        return "INTERNAL"
    return "PUBLIC"

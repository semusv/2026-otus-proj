"""Инкрементальный режим ingestion: дельта корпуса по sha256.

Снапшот прошлого успешного прогона живёт в PG (таблица ingest_files).
``plan_incremental`` - чистая функция: сопоставляет снапшот с текущим
состоянием каталога и возвращает категории файлов:

- ``unchanged`` - sha256 совпал: эмбеддинги/LLM не нужны (дешёвый граф-рефреш);
- ``changed``  - файл есть, хеш другой: полный путь обработки;
- ``added``    - файла не было в снапшоте: полный путь;
- ``removed``  - был в снапшоте, исчез из каталога: зачистка актов.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileSnapshot:
    """Запись о файле корпуса из прошлого прогона."""

    sha256: str
    act_ids: tuple[str, ...] = ()
    chunks_count: int = 0


@dataclass
class IncrementalPlan:
    """Дельта корпуса; ``to_process`` - файлы, требующие полного пути."""

    unchanged: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def to_process(self) -> list[str]:
        return self.changed + self.added


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def scan_files(corpus_dir: Path) -> list[Path]:
    """Упорядоченный список XML-файлов корпуса (синхронная функция, локальный листинг)."""
    return sorted(corpus_dir.glob("*.xml"))


def plan_incremental(
    previous: dict[str, FileSnapshot],
    current: dict[str, str],
    *,
    full: bool = False,
) -> IncrementalPlan:
    """Сопоставляет снапшот (filename -> FileSnapshot) с текущими хешами.

    ``full=True`` игнорирует совпадения хешей: все файлы каталога идут в
    ``changed`` (форс-реэмбеддинг при смене чанкера/модели/процентов грифа).
    """
    plan = IncrementalPlan()
    plan.removed = sorted(set(previous) - set(current))
    if full:
        plan.changed = sorted(current)
        return plan
    for name, sha in current.items():
        prev = previous.get(name)
        if prev is None:
            plan.added.append(name)
        elif prev.sha256 != sha:
            plan.changed.append(name)
        else:
            plan.unchanged.append(name)
    return plan

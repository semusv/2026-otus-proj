"""Парсер XML-файлов RusLawOD -> ParsedAct (чистая функция, без I/O).

Особенности реальной схемы корпуса (проверено на corpus_test/, 100 файлов):
- метаданные в ``<meta><identification>`` в атрибутах ``*IPS val="..."``;
- keywords: CSV в одном атрибуте ``keywordsByIPS val="A, B, C"``; ~19% файлов пустые;
- классификатор: пары ``код$Название$код$Название...`` в атрибуте внутри обёртки
  ``<reference>`` (единственное число); ~18% пустые;
- статусы требуют нормализации (встречается латинская «c» в «Действует c изменениями»);
- ссылки на другие акты присутствуют в тексте в экранированном виде
  ``&lt;ref nd="ID"&gt;`` (~20% файлов) и после XML-парсинга выглядят как
  литеральные теги ``<ref nd="ID">`` внутри текстового узла.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from xml.etree import ElementTree

# Латинские буквы-двойники кириллических, встречающиеся в статусах
_CONFUSABLES = str.maketrans(
    {"c": "с", "e": "е", "o": "о", "a": "а", "x": "х", "p": "р",
     "C": "С", "E": "Е", "O": "О", "A": "А", "X": "Х", "P": "Р"}
)

_REF_TAG_RE = re.compile(r'<ref\s+nd="(\d+)"\s*>')
_DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y")


@dataclass(frozen=True)
class ParsedAct:
    """Акт после разбора XML: метаданные + необработанный (но декодированный) текст.

    ``text`` содержит литеральные теги ``<ref nd="...">``/``</ref>`` и прочий мусор
    разметки - очистка выполняется отдельным модулем :mod:`app.ingestion.cleaner`.
    """

    id: str
    title: str
    doc_number: str
    date: str | None
    status_raw: str
    status: str
    authority: str
    doc_type: str
    issued_by: str
    keywords: tuple[str, ...]
    topics: tuple[str, ...]
    ref_ids: tuple[str, ...]
    text: str


def normalize_status(raw: str) -> str:
    """Нормализует статус: латинские буквы-двойники -> кириллица, схлопывание пробелов."""
    return " ".join(raw.translate(_CONFUSABLES).split())


def normalize_date(raw: str) -> str | None:
    """dd.mm.yyyy -> ISO yyyy-mm-dd; нераспознанное значение -> None (не падаем)."""
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _split_csv(raw: str) -> tuple[str, ...]:
    items: list[str] = []
    for part in raw.split(","):
        value = part.strip()
        if value and value not in items:
            items.append(value)
    return tuple(items)


def _split_classifier(raw: str) -> tuple[str, ...]:
    """Пары 'код$Название' -> названия тем (без дублей, порядок сохраняется)."""
    parts = [p.strip() for p in raw.split("$")]
    topics: list[str] = []
    for i in range(0, len(parts) - 1, 2):
        name = parts[i + 1].strip()
        if name and name not in topics:
            topics.append(name)
    return tuple(topics)


def _extract_ref_ids(text: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for match in _REF_TAG_RE.finditer(text):
        seen.setdefault(match.group(1), None)
    return tuple(seen)


def parse_act(xml_content: bytes | str) -> ParsedAct:
    """Разбирает один XML-файл RusLawOD. Отсутствие id - ошибка данных."""
    root = ElementTree.fromstring(xml_content)

    def _val(path: str) -> str:
        node = root.find(path)
        return (node.get("val") or "").strip() if node is not None else ""

    act_id = _val("meta/identification/pravogovruNd")
    if not act_id:
        raise ValueError("В XML отсутствует pravogovruNd (идентификатор акта)")

    status_raw = _val("meta/identification/statusIPS")
    date_iso = normalize_date(_val("meta/identification/docdateIPS"))
    doc_type = _val("meta/identification/doc_typeIPS")
    title = _val("meta/identification/headingIPS")

    text_node = root.find("body/textIPS")
    text = text_node.text or "" if text_node is not None else ""
    if not title:
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        title = first_line or f"{doc_type} от {_val('meta/identification/docdateIPS')}"

    return ParsedAct(
        id=act_id,
        title=" ".join(title.split()),
        doc_number=_val("meta/identification/docNumberIPS"),
        date=date_iso,
        status_raw=status_raw,
        status=normalize_status(status_raw),
        authority=_val("meta/identification/doc_author_normal_formIPS"),
        doc_type=doc_type,
        issued_by=_val("meta/identification/issuedByIPS"),
        keywords=_split_csv(_val("meta/keywords/keywordsByIPS")),
        topics=_split_classifier(_val("meta/reference/classifierByIPS")),
        ref_ids=_extract_ref_ids(text),
        text=text,
    )

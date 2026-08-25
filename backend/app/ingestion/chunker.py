"""Чанкер текста акта: разбивка по «Статья N.» с фолбэком на абзацы.

Логика:
1. Текст режется на секции по заголовкам ``Статья N.`` (преамбула до первой статьи -
   отдельная секция); строка вида «Статья 2 не приводится...» заголовком НЕ считается
   (нет точки сразу после номера).
2. Секция атомарна: одна статья = один чанк (точность цитат важнее «плотности» чанков).
3. Секция длиннее max_chars дробится по абзацам с перекрытием overlap_chars;
   продолжения получают префикс заголовка статьи для сохранения контекста эмбеддинга.
4. Если заголовков статей нет вовсе - весь текст дробится по абзацам.
"""

import re
from dataclasses import dataclass

_ARTICLE_HEAD_RE = re.compile(r"^[ \t]*Статья\s+(\d+)\.[ \t]*", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    chunk_no: int
    text: str


def _split_sections(text: str) -> list[str]:
    """Секции: преамбула (если есть) + блоки по заголовкам «Статья N.»."""
    heads = list(_ARTICLE_HEAD_RE.finditer(text))
    if not heads:
        return [text]
    sections: list[str] = []
    preamble = text[: heads[0].start()].strip()
    if preamble:
        sections.append(preamble)
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        section = text[head.start() : end].strip()
        if section:
            sections.append(section)
    return sections


def _pack_by_paragraphs(
    text: str, heading: str, max_chars: int, overlap_chars: int
) -> list[str]:
    """Дробит длинный текст по абзацам; продолжения - с префиксом заголовка статьи."""
    budget = max_chars - (len(heading) + 1 if heading else 0)
    paragraphs = [p for p in (ln.strip() for ln in text.split("\n")) if p]

    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        # одиночный абзац длиннее бюджета режется жёстко по границе предложения
        while len(paragraph) > budget:
            take = paragraph[:budget]
            cut = take.rfind(". ")
            if cut > budget // 2:
                take = take[: cut + 1]
            if current:
                pieces.append(current)
                current = ""
            pieces.append(take.strip())
            paragraph = paragraph[len(take) :].lstrip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 1 > budget:
            pieces.append(current)
            current = current[-overlap_chars:] if overlap_chars else ""
            if len(current) + len(paragraph) + 1 > budget:
                current = ""
        current = f"{current}\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)

    return [f"{heading} {piece}".strip() if heading else piece for piece in pieces]


def chunk_act(text: str, *, max_chars: int = 1800, overlap_chars: int = 200) -> list[Chunk]:
    """Чанкует очищенный текст акта. Возвращает список чанков с номерами с нуля."""
    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    for section in _split_sections(text):
        head_match = _ARTICLE_HEAD_RE.match(section)
        heading = f"{section[: head_match.end()].strip()}" if head_match else ""
        if len(section) > max_chars:
            body = section[head_match.end() :].strip() if head_match else section
            chunks.extend(_pack_by_paragraphs(body, heading, max_chars, overlap_chars))
        else:
            chunks.append(section)
    return [Chunk(chunk_no=i, text=c.strip()) for i, c in enumerate(chunks)]

"""Очистка текста акта от артефактов разметки RusLawOD.

На вход - декодированный текст из ``<textIPS>`` (после ElementTree экранированные
теги выглядят литеральными): ``<ref nd="ID">текст</ref>``, ``<span class="cmd"/>``
и т.п. На выходе - чистый текст, строки-абзацы разделены ``\\n``.
"""

import re

_REF_OPEN_RE = re.compile(r'<ref\s+nd="\d+"\s*>', re.IGNORECASE)
_REF_CLOSE_RE = re.compile(r"</ref\s*>", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^<>]*>")


def clean_text(raw: str) -> str:
    """Снимает теги разметки, нормализует пробелы в строках, выкидывает пустые строки."""
    text = _REF_OPEN_RE.sub("", raw)
    text = _REF_CLOSE_RE.sub("", text)
    # остаточные артефакты: <span .../>, <br> и прочие одиночные/парные теги
    text = _ANY_TAG_RE.sub(" ", text)

    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)

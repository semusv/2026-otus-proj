"""Unit-тесты cleaner + chunker (чистые функции, граничные случаи корпуса)."""

import pytest
from app.ingestion.chunker import chunk_act
from app.ingestion.cleaner import clean_text

pytestmark = pytest.mark.unit


class TestCleaner:
    def test_ref_tags_removed_inner_text_kept(self) -> None:
        raw = 'Статья 1. Утвердить <ref nd="102010101">Гражданский кодекс</ref> с 1 октября.'
        assert clean_text(raw) == "Статья 1. Утвердить Гражданский кодекс с 1 октября."

    def test_span_and_br_artifacts_removed(self) -> None:
        raw = 'Первая строка<br>\n<span class="cmd"/>Вторая строка</span>'
        assert clean_text(raw) == "Первая строка\nВторая строка"

    def test_whitespace_collapsed_empty_lines_dropped(self) ->None:
        raw = "  Статья  1.   Текст.  \n\n\n\n   Статья 2. Другой текст \n"
        assert clean_text(raw) == "Статья 1. Текст.\nСтатья 2. Другой текст"

    def test_plain_text_untouched(self) -> None:
        assert clean_text("Просто текст") == "Просто текст"


class TestChunkerArticles:
    def test_articles_are_separate_chunks(self) -> None:
        text = (
            "Преамбула закона.\n"
            "Статья 1. Первая статья.\n"
            "Статья 2. Вторая статья."
        )
        chunks = chunk_act(text, max_chars=100, overlap_chars=0)
        assert [c.text for c in chunks] == [
            "Преамбула закона.",
            "Статья 1. Первая статья.",
            "Статья 2. Вторая статья.",
        ]
        assert [c.chunk_no for c in chunks] == [0, 1, 2]

    def test_short_sections_stay_atomic(self) -> None:
        text = "Статья 1. Коротко.\nСтатья 2. Тоже коротко."
        chunks = chunk_act(text, max_chars=200, overlap_chars=0)
        assert [c.text for c in chunks] == ["Статья 1. Коротко.", "Статья 2. Тоже коротко."]

    def test_not_a_heading_without_dot(self) -> None:
        # «Статья 2 не приводится...» - НЕ заголовок (нет точки после номера)
        text = "Статья 1. Всё в одной статье.\nСтатья 2 не приводится, как утратившая силу."
        chunks = chunk_act(text, max_chars=200, overlap_chars=0)
        assert len(chunks) == 1


class TestChunkerFallback:
    def test_empty_text(self) -> None:
        assert chunk_act("") == []
        assert chunk_act("   \n ") == []

    def test_no_articles_paragraph_fallback(self) -> None:
        text = "\n".join(f"Абзац номер {i} с текстом распоряжения." for i in range(10))
        chunks = chunk_act(text, max_chars=80, overlap_chars=0)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.text) <= 80
            assert chunk.text.startswith("Абзац")

    def test_long_article_split_with_heading_prefix(self) -> None:
        body = "\n".join(
            f"Пункт {i} длинной статьи раскрывает предмет регулирования." for i in range(30)
        )
        text = f"Статья 4. Длинная статья\n{body}"
        chunks = chunk_act(text, max_chars=250, overlap_chars=50)
        assert len(chunks) > 2
        assert all(c.text.startswith("Статья 4.") for c in chunks), (
            "продолжения сохраняют заголовок"
        )
        assert all(len(c.text) <= 252 for c in chunks)

    def test_huge_single_line_hard_split_on_sentence(self) -> None:
        sentence = "Предложение о регулировании. "
        text = f"Статья 9. Одно предложение много раз\n{sentence * 60}"
        chunks = chunk_act(text, max_chars=300, overlap_chars=0)
        assert all(len(c.text) <= 303 for c in chunks)
        joined = " ".join(c.text for c in chunks)
        assert "Предложение о регулировании" in joined

    def test_chunk_numbers_sequential(self) -> None:
        text = "\n".join(f"Статья {i}. Содержание статьи номер {i}." for i in range(1, 6))
        chunks = chunk_act(text, max_chars=120, overlap_chars=0)
        assert [c.chunk_no for c in chunks] == list(range(len(chunks)))

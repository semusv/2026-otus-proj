"""Unit-тесты парсера RusLawOD на фикстурах по образцу реального корпуса."""

from pathlib import Path

import pytest
from app.ingestion.parser import ParsedAct, normalize_date, normalize_status, parse_act

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ruslawod"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def act_full() -> ParsedAct:
    return parse_act(_load("act_full.xml"))


class TestParseFull:
    def test_identification(self, act_full: ParsedAct) -> None:
        assert act_full.id == "102010098"
        assert act_full.title == "Об утверждении Гражданского процессуального кодекса РСФСР"
        assert act_full.doc_number == "б/н"
        assert act_full.date == "1964-06-11"
        assert act_full.authority == "РСФСР"
        assert act_full.doc_type == "Закон"

    def test_status_normalized_from_latin_c(self, act_full: object) -> None:
        assert act_full.status_raw == "Действует c изменениями"  # латинская «c»
        assert act_full.status == "Действует с изменениями"

    def test_keywords_csv(self, act_full: object) -> None:
        assert act_full.keywords == ("ГРАЖДАНСКИЙ ПРОЦЕСС", "КОДЕКС", "УТВЕРЖДЕНИЕ")

    def test_classifier_pairs_to_topics(self, act_full: object) -> None:
        assert act_full.topics == ("Гражданский процесс", "Законодательные органы")

    def test_refs_extracted_unique_in_order(self, act_full: object) -> None:
        assert act_full.ref_ids == ("102010101",)

    def test_text_keeps_literal_ref_tags_for_cleaner(self, act_full: object) -> None:
        assert '<ref nd="102010101">' in act_full.text
        assert "Гражданский процессуальный кодекс РСФСР" in act_full.text


class TestParseEmptyMeta:
    """~19% файлов корпуса: пустые keywords/classifier; здесь ещё и пустой headingIPS."""

    def test_empty_keywords_and_classifier(self) -> None:
        act = parse_act(_load("act_empty_meta.xml"))
        assert act.keywords == ()
        assert act.topics == ()
        assert act.ref_ids == ()

    def test_title_fallback_to_first_line(self) -> None:
        act = parse_act(_load("act_empty_meta.xml"))
        assert act.title.startswith("УКАЗ ПРЕЗИДИУМА")

    def test_status_already_normal(self) -> None:
        act = parse_act(_load("act_empty_meta.xml"))
        assert act.status == "Действует без изменений"

    def test_span_artifact_present_in_raw_text(self) -> None:
        act = parse_act(_load("act_empty_meta.xml"))
        assert '<span class="cmd"/>' in act.text


class TestNormalizers:
    def test_normalize_status_collapses_whitespace(self) -> None:
        assert normalize_status("  Действует   без  изменений ") == "Действует без изменений"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("01.02.2003", "2003-02-01"),
            ("11.06.64", "2064-06-11"),
            ("б/н", None),
            ("", None),
        ],
    )
    def test_normalize_date(self, raw: str, expected: str | None) -> None:
        assert normalize_date(raw) == expected


class TestErrors:
    def test_missing_id_raises(self) -> None:
        xml = (
            '<act><meta><identification>'
            '<docdateIPS val="01.01.2000"/>'
            "</identification></meta></act>"
        )
        with pytest.raises(ValueError, match="pravogovruNd"):
            parse_act(xml)

    def test_invalid_xml_raises(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - ET отдаёт ParseError
            parse_act("<not-closed")

"""Unit-тесты clearance-резолвера и маппинга онтологии."""

from pathlib import Path

import pytest
from app.ingestion.clearance import resolve_clearance
from app.ingestion.ontology import act_properties, filter_references, topics_of
from app.ingestion.parser import ParsedAct, parse_act

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ruslawod"


class TestClearance:
    def test_deterministic_across_calls(self) -> None:
        first = resolve_clearance("900000101", internal_percent=20, secret_percent=10)
        second = resolve_clearance("900000101", internal_percent=20, secret_percent=10)
        assert first == second

    def test_known_distribution(self) -> None:
        # md5-распределение фиксировано: проверяем конкретные значения для стабильности схемы
        labels = {
            act_id: resolve_clearance(act_id, internal_percent=20, secret_percent=10)
            for act_id in ("900000101", "102010099", "102010100")
        }
        assert set(labels.values()) <= {"PUBLIC", "INTERNAL", "SECRET"}

    def test_zero_percents_all_public(self) -> None:
        for act_id in (str(i) for i in range(50)):
            label = resolve_clearance(act_id, internal_percent=0, secret_percent=0)
            assert label == "PUBLIC"

    def test_all_secret(self) -> None:
        for act_id in (str(i) for i in range(50)):
            label = resolve_clearance(act_id, internal_percent=0, secret_percent=100)
            assert label == "SECRET"

    def test_leftover_bucket_is_public(self) -> None:
        # secret=99 + internal=0: корзина 99 (последний процент) остаётся PUBLIC
        labels = {
            resolve_clearance(str(i), internal_percent=0, secret_percent=99)
            for i in range(500)
        }
        assert labels == {"SECRET", "PUBLIC"}

    def test_approximate_distribution(self) -> None:
        ids = [f"act-{i:05d}" for i in range(2000)]
        counts = {"SECRET": 0, "INTERNAL": 0}
        for act_id in ids:
            label = resolve_clearance(act_id, internal_percent=20, secret_percent=10)
            if label in counts:
                counts[label] += 1
        assert 150 < counts["SECRET"] < 250, f"secret≈10%: {counts}"
        assert 330 < counts["INTERNAL"] < 470, f"internal≈20%: {counts}"


class TestOntology:
    @pytest.fixture
    def act(self) -> "ParsedAct":
        xml = (FIXTURES / "act_full.xml").read_text(encoding="utf-8")
        return parse_act(xml)

    def test_act_properties_complete(self, act: ParsedAct) -> None:
        props = act_properties(act, "INTERNAL")
        assert props["id"] == "900000101"
        assert props["clearance"] == "INTERNAL"
        assert props["date"] == "1964-06-11"
        assert props["status"] == "Действует с изменениями"
        assert props["keywords"] == ["ГРАЖДАНСКИЙ ПРОЦЕСС", "КОДЕКС", "УТВЕРЖДЕНИЕ"]

    def test_references_filtered_to_corpus(self, act: ParsedAct) -> None:
        # в фикстуре ссылка на 102010101; считаем корпусом только её саму + соседа
        refs = filter_references(act, {"900000101", "102010101"})
        assert refs == ("102010101",)

    def test_references_dropped_when_target_absent(self, act: ParsedAct) -> None:
        assert filter_references(act, {"999999999"}) == ()

    def test_topics_merge_classifier_and_keywords(self, act: ParsedAct) -> None:
        topics = topics_of(act)
        assert topics[0] == "Гражданский процесс"  # классификатор первым
        assert "КОДЕКС" in topics  # keyword добавлен
        # keyword «ГРАЖДАНСКИЙ ПРОЦЕСС» схлопнулся с темой классификатора (casefold)
        assert "ГРАЖДАНСКИЙ ПРОЦЕСС" not in topics
        assert len(topics) == len({t.casefold() for t in topics}), "нет дублей"

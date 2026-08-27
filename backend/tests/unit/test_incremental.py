"""Юнит-тесты инкрементального ingestion: plan_incremental + валидация upload.

Инкрементальный режим (бэклог п.1, задача «Корпус»): снапшот корпуса по sha256.
"""

import pytest
from app.api.admin import _safe_upload_name
from app.ingestion.incremental import FileSnapshot, plan_incremental

pytestmark = pytest.mark.unit


class TestPlanIncremental:
    def test_first_run_all_added(self) -> None:
        plan = plan_incremental({}, {"a.xml": "h1", "b.xml": "h2"})
        assert plan.added == ["a.xml", "b.xml"]
        assert plan.to_process == ["a.xml", "b.xml"]
        assert plan.unchanged == []
        assert plan.changed == []
        assert plan.removed == []

    def test_unchanged_detected_by_hash(self) -> None:
        prev = {"a.xml": FileSnapshot(sha256="h1", act_ids=("1",), chunks_count=3)}
        plan = plan_incremental(prev, {"a.xml": "h1"})
        assert plan.unchanged == ["a.xml"]
        assert plan.to_process == []

    def test_changed_by_hash(self) -> None:
        prev = {"a.xml": FileSnapshot(sha256="old")}
        plan = plan_incremental(prev, {"a.xml": "new"})
        assert plan.changed == ["a.xml"]
        assert plan.to_process == ["a.xml"]

    def test_removed_when_absent_on_disk(self) -> None:
        prev = {"a.xml": FileSnapshot("h1"), "b.xml": FileSnapshot("h2")}
        plan = plan_incremental(prev, {"b.xml": "h2"})
        assert plan.removed == ["a.xml"]
        assert plan.unchanged == ["b.xml"]

    def test_full_ignores_matching_hashes(self) -> None:
        prev = {"a.xml": FileSnapshot("h1")}
        plan = plan_incremental(prev, {"a.xml": "h1", "b.xml": "h2"}, full=True)
        assert plan.changed == ["a.xml", "b.xml"]
        assert plan.unchanged == []
        assert plan.added == []
        assert plan.removed == []

    def test_all_categories_combined(self) -> None:
        prev = {
            "same.xml": FileSnapshot("s"),
            "mod.xml": FileSnapshot("old"),
            "gone.xml": FileSnapshot("x"),
        }
        plan = plan_incremental(prev, {"same.xml": "s", "mod.xml": "new", "fresh.xml": "f"})
        assert plan.unchanged == ["same.xml"]
        assert plan.changed == ["mod.xml"]
        assert plan.added == ["fresh.xml"]
        assert plan.removed == ["gone.xml"]
        assert plan.to_process == ["mod.xml", "fresh.xml"]

    def test_snapshot_is_frozen(self) -> None:
        snap = FileSnapshot(sha256="h", act_ids=("a", "b"), chunks_count=2)
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            snap.sha256 = "other"  # type: ignore[misc]


class TestSafeUploadName:
    @pytest.mark.parametrize("raw", ["act.xml", "акт-1.XML", "a.b.c.xml", "with space.xml"])
    def test_valid(self, raw: str) -> None:
        assert _safe_upload_name(raw) == raw

    @pytest.mark.parametrize(
        "raw",
        [None, "", "..", ".", "a/b.xml", "..\\evil.xml", "back\\slash.xml", "x" * 256],
    )
    def test_invalid(self, raw: str | None) -> None:
        assert _safe_upload_name(raw) == ""

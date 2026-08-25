"""Тесты структурного JSON-логирования и инъекции trace_id/request_id."""

import json
import logging
from io import StringIO

import pytest
from app.core.context import set_request_id, set_trace_id
from app.core.logging import ContextFilter, JsonFormatter, configure_logging

pytestmark = pytest.mark.unit


def _make_logger() -> tuple[logging.Logger, StringIO]:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(ContextFilter())
    log = logging.getLogger("app.test.json")
    log.handlers.clear()
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log, stream


def test_log_line_is_json_with_correlation_ids() -> None:
    set_trace_id("trace-abc")
    set_request_id("req-123")
    log, stream = _make_logger()
    log.info("Проверка %s", "логов")
    record = json.loads(stream.getvalue())
    assert record["message"] == "Проверка логов"
    assert record["trace_id"] == "trace-abc"
    assert record["request_id"] == "req-123"
    assert record["level"] == "INFO"
    assert "ts" in record


def test_defaults_when_no_context() -> None:
    set_trace_id("-")
    set_request_id("-")
    log, stream = _make_logger()
    log.warning("без контекста")
    record = json.loads(stream.getvalue())
    assert record["trace_id"] == "-"
    assert record["request_id"] == "-"


def test_exception_serialized() -> None:
    log, stream = _make_logger()
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("упс")
    record = json.loads(stream.getvalue())
    assert "ValueError: boom" in record["exc_info"]


def test_configure_logging_sets_json_handler(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging("DEBUG")
    root = logging.getLogger()
    assert any(isinstance(h.formatter, JsonFormatter) for h in root.handlers)
    assert root.level == logging.DEBUG

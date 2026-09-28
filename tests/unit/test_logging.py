import json
import logging

from charteros.shared.context import bind_correlation_id, reset_correlation_id
from charteros.shared.logging import JsonFormatter


def test_json_formatter_emits_structured_context() -> None:
    formatter = JsonFormatter(service_name="test-service")
    record = logging.LogRecord(
        name="charteros.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    setattr(record, "event", "test_event")
    token = bind_correlation_id("7eb2f9b8-fad3-4c62-b0cb-b33f582ac51a")

    try:
        payload = json.loads(formatter.format(record))
    finally:
        reset_correlation_id(token)

    assert payload["service"] == "test-service"
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello"
    assert payload["event"] == "test_event"
    assert payload["correlation_id"] == "7eb2f9b8-fad3-4c62-b0cb-b33f582ac51a"

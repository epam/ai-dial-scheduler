"""Tests for the trace/span structlog processor (_add_trace_span_ids)."""

from opentelemetry.sdk.trace import TracerProvider


class TestAddTraceSpanIds:
    """Tests for _add_trace_span_ids structlog processor."""

    def test_adds_trace_id_and_span_id_when_span_active(self):
        from src.lib.utils.logging import _add_trace_span_ids

        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            result = _add_trace_span_ids(None, None, {})

        assert "trace_id" in result
        assert "span_id" in result
        assert len(result["trace_id"]) == 32
        assert len(result["span_id"]) == 16
        assert all(c in "0123456789abcdef" for c in result["trace_id"])
        assert all(c in "0123456789abcdef" for c in result["span_id"])

    def test_trace_id_matches_active_span_context(self):
        from opentelemetry import trace

        from src.lib.utils.logging import _add_trace_span_ids

        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("check-span"):
            span_ctx = trace.get_current_span().get_span_context()
            result = _add_trace_span_ids(None, None, {})

        assert result["trace_id"] == format(span_ctx.trace_id, "032x")
        assert result["span_id"] == format(span_ctx.span_id, "016x")

    def test_no_fields_added_when_no_active_span(self):
        from src.lib.utils.logging import _add_trace_span_ids

        result = _add_trace_span_ids(None, None, {})

        assert "trace_id" not in result
        assert "span_id" not in result

    def test_existing_trace_id_not_overwritten(self):
        from src.lib.utils.logging import _add_trace_span_ids

        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            result = _add_trace_span_ids(None, None, {"trace_id": "existing-trace-id"})

        assert result["trace_id"] == "existing-trace-id"

    def test_existing_span_id_not_overwritten(self):
        from src.lib.utils.logging import _add_trace_span_ids

        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            result = _add_trace_span_ids(None, None, {"span_id": "existing-span-id"})

        assert result["span_id"] == "existing-span-id"

    def test_returns_valid_dict_with_no_span(self):
        from src.lib.utils.logging import _add_trace_span_ids

        event_dict = {"event": "something happened", "key": "value"}
        result = _add_trace_span_ids(None, None, event_dict)

        assert result["event"] == "something happened"
        assert result["key"] == "value"

    def test_returns_event_dict_with_span(self):
        from src.lib.utils.logging import _add_trace_span_ids

        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span"):
            event_dict = {"event": "hello", "level": "info"}
            result = _add_trace_span_ids(None, None, event_dict)

        assert result["event"] == "hello"
        assert result["level"] == "info"
        assert "trace_id" in result
        assert "span_id" in result

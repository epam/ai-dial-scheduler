"""Tests for the trace/span structlog processor (_add_trace_span_ids) and the log-level filters."""

import logging

from opentelemetry.sdk.trace import TracerProvider

from src.lib.exceptions.errors import ScheduleRunError


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


def _job_error_record(exception: BaseException | None) -> logging.LogRecord:
    """Build the record APScheduler emits when a job raises."""
    record = logging.LogRecord(
        name="apscheduler.executors.default",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg='Job "%s" raised an exception',
        args=("job-1",),
        exc_info=(type(exception), exception, exception.__traceback__) if exception else None,
    )
    return record


class TestExpectedJobErrorFilter:
    """A failed run is the target's fault, not the scheduler's -- it must not page as an error."""

    def test_schedule_run_error_is_downgraded_to_warning(self):
        from src.lib.utils.logging import _ExpectedJobErrorFilter

        # Mirrors the real chain: DIAL 404s the OBO endpoint, the worker raises ScheduleRunError,
        # APScheduler logs the unhandled exception at ERROR.
        error = ScheduleRunError(
            "OBO credentials request failed with status 404", schedule_id="sched-1", run_id="run-1"
        )
        record = _job_error_record(error)

        assert _ExpectedJobErrorFilter().filter(record) is True
        assert record.levelno == logging.WARNING
        assert record.levelname == "WARNING"
        # The traceback survives the downgrade -- diagnosability is unchanged.
        assert record.exc_info is not None

    def test_unexpected_exception_stays_at_error(self):
        from src.lib.utils.logging import _ExpectedJobErrorFilter

        record = _job_error_record(RuntimeError("connection pool exhausted"))

        assert _ExpectedJobErrorFilter().filter(record) is True
        assert record.levelno == logging.ERROR
        assert record.levelname == "ERROR"

    def test_record_without_exception_is_untouched(self):
        from src.lib.utils.logging import _ExpectedJobErrorFilter

        record = _job_error_record(None)

        assert _ExpectedJobErrorFilter().filter(record) is True
        assert record.levelno == logging.ERROR

    def test_filter_is_wired_to_the_apscheduler_executor_logger(self):
        from src.lib.utils.logging import LoggingConfig

        config = LoggingConfig.get_config()

        assert config["loggers"]["apscheduler.executors.default"]["filters"] == ["expected_job_error"]
        assert "expected_job_error" in config["filters"]

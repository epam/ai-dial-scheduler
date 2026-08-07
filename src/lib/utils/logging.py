from collections import OrderedDict
from collections.abc import MutableMapping
import logging
import logging.config
import os
from typing import Any

from opentelemetry import trace
import structlog
from structlog.stdlib import ProcessorFormatter

from src.lib.exceptions.errors import ScheduleRunError
from src.lib.settings import get_settings


class _ExpectedJobErrorFilter(logging.Filter):
    """Emit APScheduler's job-exception record at WARNING when the failure is an expected one.

    A run that fails because the target or DIAL rejected it (a 404 from the OBO credentials
    endpoint, a 500 from the user's URL) raises ``ScheduleRunError`` out of the job. The worker has
    already logged it at warning and persisted a diagnosable run error, but APScheduler then logs
    the same failure again at ERROR with a traceback — turning every remote or user fault into an
    error-level page. Only the scheduler's own unexpected faults belong at ERROR, so downgrade this
    one record instead of silencing it: the traceback is still emitted, just at warning.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        exception = record.exc_info[1] if record.exc_info else None
        if record.levelno == logging.ERROR and isinstance(exception, ScheduleRunError):
            record.levelno = logging.WARNING
            record.levelname = logging.getLevelName(logging.WARNING)
        return True


class _HealthCheckFilter(logging.Filter):
    _FILTERED_PATHS = ("/ready", "/health")

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access args: (client_addr, method, full_path, http_version, status_code)
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = args[2]
            if isinstance(path, str):
                return not any(path.startswith(p) for p in self._FILTERED_PATHS)
        return True


def _add_trace_span_ids(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Inject trace_id and span_id when a valid OTel span is active."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        if "trace_id" not in event_dict:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
        if "span_id" not in event_dict:
            event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def _order_keys(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> OrderedDict:
    format_keys = ["timestamp", "level", "event", "logger"]
    ordered_keys = OrderedDict()
    for key in format_keys:
        if key in event_dict:
            ordered_keys[key] = event_dict[key]
    for key, value in event_dict.items():
        if key not in ordered_keys:
            ordered_keys[key] = value
    return ordered_keys


class LoggingConfig:
    @staticmethod
    def get_config() -> dict:
        settings = get_settings()
        log_level = (settings.log_level or "INFO").upper()
        otel_log_level = os.getenv("OTEL_LOG_LEVEL", "ERROR")

        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structured": {
                    "()": ProcessorFormatter,
                    "processors": [
                        structlog.contextvars.merge_contextvars,
                        structlog.stdlib.add_log_level,
                        structlog.stdlib.add_logger_name,
                        _add_trace_span_ids,
                        structlog.processors.TimeStamper(fmt="iso"),
                        structlog.processors.format_exc_info,
                        ProcessorFormatter.remove_processors_meta,
                        _order_keys,
                        structlog.processors.JSONRenderer(),
                    ],
                    "foreign_pre_chain": [
                        structlog.contextvars.merge_contextvars,
                        structlog.stdlib.add_log_level,
                        structlog.stdlib.add_logger_name,
                        _add_trace_span_ids,
                        structlog.processors.TimeStamper(fmt="iso"),
                    ],
                },
            },
            "filters": {
                "health_check": {
                    "()": _HealthCheckFilter,
                },
                "expected_job_error": {
                    "()": _ExpectedJobErrorFilter,
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "structured",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": log_level,
            },
            "loggers": {
                settings.app_name: {"level": log_level, "propagate": True},
                "uvicorn": {"level": log_level, "propagate": True},
                "uvicorn.error": {"level": log_level, "propagate": True},
                "uvicorn.access": {"level": log_level, "propagate": True, "filters": ["health_check"]},
                "apscheduler.executors.default": {
                    "level": log_level,
                    "propagate": True,
                    "filters": ["expected_job_error"],
                },
                "httpx": {"level": log_level, "propagate": True},
                "httpcore": {"level": log_level, "propagate": True},
                "opentelemetry": {"level": otel_log_level, "propagate": True},
            },
        }


def configure_logging() -> None:
    settings = get_settings()
    logging.config.dictConfig(LoggingConfig.get_config())

    level_name = (settings.log_level or "INFO").upper()
    level = logging._nameToLevel.get(level_name, logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            _add_trace_span_ids,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


# ISSUE-1 fix: no get_settings() argument — structlog.get_logger() is lazy
logger = structlog.get_logger()

import pytest
import structlog

from src.lib.utils.logging import logger as _app_logger


@pytest.fixture(autouse=True)
def reset_structlog() -> None:
    """Reset structlog to defaults before each test.

    configure_logging() sets cache_logger_on_first_use=True with stdlib LoggerFactory.
    On first use, BoundLoggerLazyProxy stores self.bind = finalized_bind (an instance
    attribute) wrapping a stdlib-backed BoundLogger with processors frozen at cache time.
    capture_logs() modifies _CONFIG.default_processors in-place, but the cached
    BoundLogger ignores that — its processors were fixed at cache time.

    Fix: reset_defaults() reverts the config; popping "bind" from the proxy's __dict__
    discards the frozen instance so the next log call picks up the reset configuration.
    """
    structlog.reset_defaults()
    _app_logger.__dict__.pop("bind", None)
    structlog.contextvars.clear_contextvars()

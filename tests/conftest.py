import os

import pytest
import structlog

from src.lib.utils.logging import logger as _app_logger


@pytest.fixture(scope="session", autouse=True)
def set_required_env_vars():
    """Provide minimal env vars so Settings and DatabaseSettings can be instantiated without a .env file."""
    defaults = {
        "DIAL_URL": "http://localhost:8080",
        "DIAL_API_KEY": "test-api-key",
        "DB_PASSWORD": "test-password",
    }
    existing = {k: os.environ[k] for k in defaults if k in os.environ}
    for k, v in defaults.items():
        if k not in os.environ:
            os.environ[k] = v

    from src.lib.settings import get_settings

    get_settings.cache_clear()

    yield

    for k in defaults:
        if k in existing:
            os.environ[k] = existing[k]
        else:
            os.environ.pop(k, None)

    get_settings.cache_clear()


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

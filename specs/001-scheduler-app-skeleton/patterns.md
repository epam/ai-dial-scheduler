# Implementation constraints and patterns

## pyproject.toml

### Python version is 3.14 throughout — `requires-python`, `target-version` (ruff), and `python_version` (mypy) must all be 3.14. `asyncio_mode = "auto"` is required for pytest-asyncio. `pythonpath = ["."]` so imports resolve as `from src.xxx import ...`. Dev dependency versions are shown as `x.x.x` — substitute the latest stable release but keep the major version locked (no `>=major+1`).

pyproject.toml:
```toml
[project]
name = "aidial-scheduler"
version = "0.1.0"
description = "Dial Scheduler backend"
readme = "README.md"
requires-python = ">=3.14,<3.15"

authors = [
    { name = "Your Name", email = "your.email@example.com" }
]


[dependency-groups]
dev = [
    "pre-commit>=x.x.x,<next-major",
    "ruff>=x.x.x,<next-major",
    "mypy>=x.x.x,<next-major",

    "pytest>=x.x.x,<next-major",
    "pytest-cov>=x.x.x,<next-major",
    "pytest-mock>=x.x.x,<next-major",
    "pytest-sugar>=x.x.x,<next-major",
    "pytest-asyncio>=x.x.x,<next-major",
]


[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
package = false


[tool.ruff]
line-length = 120
src = ["src", "tests"]
target-version = "py314"

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
skip-magic-trailing-comma = false
line-ending = "auto"

[tool.ruff.lint]
select = ["E", "F", "I"]
ignore = []

[tool.ruff.lint.isort]
combine-as-imports = true
force-single-line = false
force-sort-within-sections = true
known-first-party = ["src"]
lines-after-imports = 2
split-on-trailing-comma = true


[tool.pytest.ini_options]
pythonpath = ["."]
asyncio_mode = "auto"


[tool.mypy]
python_version = "3.14"
namespace_packages = true
show_error_codes = true
ignore_missing_imports = true
plugins = ["pydantic.mypy"]
```

## Pre-commit (.pre-commit-config.yaml)

### Three hooks in order: general file hygiene (pre-commit-hooks), ruff lint+format, mypy type check. Ruff runs with `--fix --no-unsafe-fixes`. Mypy needs `pydantic>=2` and `fastapi` as additional dependencies so it can resolve their stubs. Revisions shown as `vx.x.x` — substitute the latest stable tag for each repo.

.pre-commit-config.yaml:
```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: vx.x.x
    hooks:
    - id: check-yaml
    - id: end-of-file-fixer
    - id: trailing-whitespace

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: vx.x.x
    hooks:
      - id: ruff-check
        args: [ "--fix", "--no-unsafe-fixes" ]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: vx.x.x
    hooks:
      - id: mypy
        additional_dependencies:
          - pydantic>=2
          - fastapi
```

## Single Docker file for migrations, api and scheduler
### One shared image for 3 services should be created. "SERVICE" env variable should decide which service is running.

Dockerfile:
```
FROM --platform=linux/amd64 python:3.14-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV APP_HOME=/home/app
ENV HOME=/home/app

WORKDIR $APP_HOME

RUN apk add --no-cache --virtual .build-deps curl alpine-sdk linux-headers unixodbc postgresql-dev gcc python3-dev musl-dev

# Install dependencies
COPY pyproject.toml uv.lock ./
RUN pip install uv==0.9.28
RUN uv sync --frozen --no-dev

COPY src/ ./src
COPY alembic.ini ./
COPY migrations/ ./migrations

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh
COPY entrypoint_api.py ./
COPY entrypoint_worker.py ./

RUN adduser -u 1001 --system --disabled-password --gecos "" app && chown -R app $APP_HOME # 1001 id also used in HELM chart

# Place executables in the environment at the front of the path
ENV PATH="$APP_HOME/.venv/bin:$PATH"

USER app

CMD ["sh", "entrypoint.sh"]
```

entrypoint.sh:
```
#!/bin/sh

echo "Running entrypoint.sh..."

# Check if SERVICE environment variable is set
if [ -z "$SERVICE" ]; then
  echo "SERVICE environment variable is not set"
  exit 1
fi

# Run the corresponding command based on the value of SERVICE
if [ "$SERVICE" = "migrations" ]; then
    echo "Triggering migrations..."
    alembic upgrade head
elif [ "$SERVICE" = "api" ]; then
    echo "Triggering api..."
    python entrypoint_api.py
elif [ "$SERVICE" = "worker" ]; then
    echo "Triggering worker..."
    python entrypoint_worker.py
else
    echo "Unknown SERVICE: $SERVICE"
    exit 1
fi
```

entrypoint_api.py:
```python
from src.api.app import run_api


if __name__ == "__main__":
    run_api()
```

entrypoint_worker.py:
```python
from src.worker.app import run_worker


if __name__ == "__main__":
    run_worker()
```

## App creation with aidial-sdk

src.api.app.py:
```python
from contextlib import asynccontextmanager

from aidial_sdk import DIALApp
from aidial_sdk.telemetry.types import TelemetryConfig
import httpx
from starlette.middleware.cors import CORSMiddleware
import uvicorn

from src.lib.db.engine import Database
from src.lib.settings import get_settings
from src.lib.utils.logging import (
    LoggingConfig,
    configure_logging,
    logger,
)


configure_logging()


@asynccontextmanager
async def lifespan(app: DIALApp):
    """Lifespan handler to create and close global HTTP Client and DB"""
    database = Database()
    database.init_engine()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=5.0, read=60.0, write=60.0, pool=5.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=20),
    ) as app_client:
        logger.info("Created global HTTP Client for the app lifespan.")
        app.state.app_client = app_client  # type: ignore[attr-defined]
        yield
    logger.info("Destroyed global HTTP Client for the app lifespan.")
    await database.dispose()


def create_api() -> DIALApp:
    settings = get_settings()
    app = DIALApp(
        dial_url=settings.dial_url,
        title=settings.app_name,
        add_healthcheck=True,
        telemetry_config=TelemetryConfig(
            service_name=settings.app_name,
        ),
        lifespan=lifespan,
        redirect_slashes=False,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app


def run_api() -> None:
    settings = get_settings()
    options: dict = {
        "host": "0.0.0.0",
        "port": settings.port,
        "log_level": settings.log_level.lower(),
        "reload": settings.env == "dev",
        "log_config": LoggingConfig.get_config(),
        "factory": True,
    }

    uvicorn.run("src.api.app:create_api", **options)
```

## Pydantic settings

### Settings should be organized in way that one service doesn't depend on settings that it do not use. So you're free to split them. The constraint is that settings should be initialized only once. That's why we wrap it with lru_cache.

src.lib.settings.py:
```python
from functools import lru_cache

from pydantic import model_validator, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.lib.db.enums import DbAuthMode, DbDriver


ENV_FILE_MODEL_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",  # ignore env. var-s in .env that aren't listed below
)


class DatabaseSettings(BaseSettings):
    model_config = ENV_FILE_MODEL_CONFIG

    db_driver: DbDriver = DbDriver.ASYNC
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "app"
    db_user: str = "app"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800
    db_pool_pre_ping: bool = True
    db_echo: bool = False
    db_auth_mode: DbAuthMode = DbAuthMode.PASSWORD

    # Optional; engine initialization decides how to use it.
    db_password: str | None = None
    db_url: str | None = None

    @model_validator(mode="after")
    def validate_auth_requirements(self) -> "DatabaseSettings":
        required_fields_by_mode: dict[DbAuthMode, tuple[str, ...]] = {
            DbAuthMode.PASSWORD: ("db_password",),
            DbAuthMode.CONNECTION_STRING: ("db_url",),
        }
        required_fields = required_fields_by_mode.get(self.db_auth_mode, ())
        missing = [field for field in required_fields if getattr(self, field) is None]
        if missing:
            fields_label = ", ".join(missing)
            raise ValueError(f"DB auth mode '{self.db_auth_mode.value}' requires: {fields_label}.")
        return self


class Settings(BaseSettings):
    model_config = ENV_FILE_MODEL_CONFIG
    
    env: str = "dev"
    app_name: str = "aidial-scheduler"
    dial_url: str
    dial_api_key: SecretStr
    port: int = 5000

    log_level: str = "INFO"

    db: DatabaseSettings = DatabaseSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

## DB resources

### In prev section regarding [App creation with aidial-sdk](#app-creation-with-aidial-sdk) we setup lifespan for application to init DB resources on startup and close on api exit. DB object should be used on both migrations with alembic and api. APScheduler 3.x `SQLAlchemyJobStore` requires a sync psycopg2 URL — it cannot use asyncpg. Pass `driver=DbDriver.SYNC` to `build_database_url` to get a psycopg2 URL from the same settings without touching `db_driver`. The async `Database` engine is never shared with APScheduler.

src.lib.db.enums.py:
```python
from enum import Enum


class DbAuthMode(str, Enum):
    PASSWORD = "password"
    CONNECTION_STRING = "connection-string"


class DbDriver(str, Enum):
    ASYNC = "postgresql+asyncpg"
    SYNC = "postgresql+psycopg2"
```

src.lib.db.engine.py:
```python
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.lib.db.enums import DbAuthMode
from src.lib.db.url import build_database_url
from src.lib.settings import get_settings
from src.lib.utils.logging import logger


class Database:
    _instance: Optional["Database"] = None

    def __new__(cls) -> "Database":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return

        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._initialized = True

    def init_engine(self, *, poolclass=None, force_fresh: bool = False) -> AsyncEngine:
        settings = get_settings()
        if self._engine is not None and not force_fresh:
            return self._engine

        if self._engine is not None and force_fresh:
            self._engine = None
            self._sessionmaker = None

        logger.info(f"Initializing a new DB engine with {settings.db.db_auth_mode} auth mode.")
        if settings.db.db_auth_mode in (DbAuthMode.CONNECTION_STRING, DbAuthMode.PASSWORD):
            engine = self._init_engine_url_based(poolclass=poolclass)
        else:
            raise ValueError(f"Unsupported DB Auth mode: {settings.db.db_auth_mode}")

        self._sessionmaker = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
            autoflush=False,
        )
        return engine

    def _init_engine_url_based(self, *, poolclass=None) -> AsyncEngine:
        settings = get_settings()
        url = build_database_url(settings)

        engine_kwargs: dict[str, object] = {
            "echo": settings.db.db_echo,
            "pool_pre_ping": settings.db.db_pool_pre_ping,
        }

        if poolclass is not None:
            engine_kwargs["poolclass"] = poolclass
        else:
            engine_kwargs["pool_size"] = settings.db.db_pool_size
            engine_kwargs["max_overflow"] = settings.db.db_max_overflow
            engine_kwargs["pool_recycle"] = settings.db.db_pool_recycle_seconds

        engine = create_async_engine(url, **engine_kwargs)

        self._engine = engine
        return engine

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self.init_engine()
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            self.init_engine()
        return self._sessionmaker

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
```

src.lib.db.session.py:
```python
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.engine import Database


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    database = Database()
    async with database.sessionmaker() as session:
        yield session
```

src.lib.db.url.py:
```python
from sqlalchemy.engine import URL

from src.lib.db.enums import DbAuthMode, DbDriver
from src.lib.settings import Settings


def build_database_url(
    settings: Settings,
    *,
    driver: DbDriver | None = None,
    hide_password: bool = False,
) -> str:
    if settings.db.db_url and settings.db.db_auth_mode == DbAuthMode.CONNECTION_STRING:
        return settings.db.db_url

    url = URL.create(
        drivername=(driver or settings.db.db_driver).value,
        username=settings.db.db_user,
        password=settings.db.db_password,
        host=settings.db.db_host,
        port=settings.db.db_port,
        database=settings.db.db_name,
    )
    return url.render_as_string(hide_password=hide_password)
```

src.lib.models.base.py:
```python
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import declarative_base, declared_attr
from sqlmodel import Field, SQLModel


# Define Alembic naming convention for constraints
# https://alembic.sqlalchemy.org/en/latest/naming.html
convention = {
    "ix": "IX_%(column_0_label)s",
    "uq": "UQ_%(table_name)s_%(column_0_name)s",
    "ck": "CK_%(table_name)s_%(constraint_name)s",
    "fk": "FK_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "PK_%(table_name)s",
}

# Create metadata with naming convention
metadata = MetaData(naming_convention=convention)

# Create a base class that uses this metadata
Base = declarative_base(metadata=metadata)

# Configure SQLModel to use this metadata
SQLModel.metadata = metadata


class BaseSQLModel(SQLModel):
    __abstract__ = True

    @declared_attr.directive
    def __tablename__(cls) -> str:  # type: ignore
        # Convert CamelCase to snake_case using list comprehension
        name = cls.__name__
        result = ["_" + char.lower() if char.isupper() and i > 0 else char.lower() for i, char in enumerate(name)]

        # Add 's' for plural
        return "".join(result) + "s"

    id: int = Field(default=None, primary_key=True)

    created_at: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore
        sa_column_kwargs={"server_default": func.now(), "nullable": False},
    )

    updated_at: datetime = Field(
        sa_type=DateTime(timezone=True),  # type: ignore
        sa_column_kwargs={
            "server_default": func.now(),
            "onupdate": func.now(),
            "nullable": False,
        },
    )

```

src.lib.models.\_\_init\_\_.py:
```python
from .base import BaseSQLModel


__all__ = ["BaseSQLModel"]
```

src.api.dependencies.db.py (ONLY USED BY API):
```python
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.session import get_session


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session
```


### Alembic migrations:

migrations.env.py:
```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection

from src.lib.db.engine import Database
from src.lib.db.url import build_database_url
from src.lib.models import BaseSQLModel
from src.lib.settings import get_settings


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = BaseSQLModel.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.
config.set_main_option("sqlalchemy.url", build_database_url(get_settings(), hide_password=True))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    app_db = Database()
    app_db.init_engine(poolclass=pool.NullPool, force_fresh=True)
    connectable = app_db.engine

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await app_db.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

script.py.mako:
```
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema."""
    ${downgrades if downgrades else "pass"}
```


## Logging (structlog)

### structlog is used for structured JSON logging. `LoggingConfig` must not define class-level variables that evaluate settings — all config is built lazily inside `get_config()`. `configure_logging()` must be called once at app startup before any loggers are used. The `logger` module-level export is safe because `structlog.get_logger()` is lazy and `get_settings()` is lru_cached.

src.lib.utils.logging.py:
```python
from collections import OrderedDict
import logging
import logging.config
import os

import structlog
from structlog.stdlib import ProcessorFormatter

from src.lib.settings import get_settings


def _order_keys(_, __, event_dict) -> OrderedDict:
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
                        structlog.processors.TimeStamper(fmt="iso"),
                    ],
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
                "uvicorn.access": {"level": log_level, "propagate": True},
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
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger(get_settings().app_name)
```

## Worker app (APScheduler)

### Worker is a separate process running an asyncio event loop. Jobs are async coroutines (they use async httpx). Use `AsyncIOScheduler` from APScheduler 3.x — it runs job coroutines on the event loop correctly. `SQLAlchemyJobStore` (psycopg2) is still sync and will briefly block the event loop during each scheduler tick (one DB query every few seconds), but this is acceptable for a cron scheduler with infrequent job firing. APScheduler 4.x is still alpha as of 2026 — do not use. `configure_logging()` is called at module level. `create_worker` is the factory; `run_worker` drives the event loop via `asyncio.run`.

src.worker.app.py:
```python
import asyncio

from src.lib.db.enums import DbDriver
from src.lib.db.url import build_database_url
from src.lib.settings import get_settings
from src.lib.utils.logging import configure_logging, logger


configure_logging()


def create_worker():
    settings = get_settings()
    sync_url = build_database_url(settings, driver=DbDriver.SYNC)
    # Use AsyncIOScheduler so job coroutines run on the event loop.
    # SQLAlchemyJobStore requires the sync psycopg2 URL above.
    ...


async def _run() -> None:
    scheduler = create_worker()
    logger.info("Starting worker.")
    scheduler.start()


def run_worker() -> None:
    asyncio.run(_run())
```




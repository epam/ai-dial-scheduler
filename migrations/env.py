import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection

from src.lib.db.engine import Database
from src.lib.db.enums import DbAuthMode
from src.lib.db.url import build_database_url
from src.lib.models.base import BaseSQLModel
import src.lib.models.schedule  # noqa: F401 — registers Schedule with BaseSQLModel.metadata
import src.lib.models.schedule_run  # noqa: F401 — registers ScheduleRun with BaseSQLModel.metadata
from src.lib.settings import DatabaseSettings


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = BaseSQLModel.metadata

_URL_BASED_MODES = (DbAuthMode.PASSWORD, DbAuthMode.CONNECTION_STRING)

_UNMANAGED_TABLES = {"apscheduler_jobs"}

_CONFIGURE_KWARGS: dict = {
    "target_metadata": target_metadata,
    "include_name": lambda name, type_, parent_names: name not in _UNMANAGED_TABLES if type_ == "table" else True,
}


def _get_db_settings() -> DatabaseSettings:
    """Lazily construct DatabaseSettings to avoid side effects at import time.

    Instantiating ``DatabaseSettings`` at module scope reads the environment and ``.env`` file,
    which can interfere with test collection and other tooling that imports this module without
    intending to initialize database configuration.
    """
    return DatabaseSettings()


def run_migrations_offline() -> None:
    db_settings = _get_db_settings()
    # Only URL-based modes can express a static connection string. Workload-identity modes
    # (gcp_iam, azure_entra) inject short-lived credentials at connect time and are handled by the
    # online path via Database().init_engine(); they have no meaningful static URL to advertise here.
    if db_settings.db_auth_mode not in _URL_BASED_MODES:
        raise RuntimeError(
            f"Offline migrations are not supported for DB auth mode "
            f"'{db_settings.db_auth_mode.value}': workload-identity modes require runtime "
            f"credentials. Run online migrations (alembic upgrade head) instead."
        )
    url = build_database_url(db_settings, hide_password=True)
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"}, **_CONFIGURE_KWARGS)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, **_CONFIGURE_KWARGS)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    app_db = Database()
    app_db.init_engine(poolclass=pool.NullPool, force_fresh=True)
    connectable = app_db.engine

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await app_db.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

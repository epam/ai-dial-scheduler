import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection

from src.lib.db.engine import Database
from src.lib.db.url import build_database_url
from src.lib.models import BaseSQLModel
from src.lib.settings import DatabaseSettings


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = BaseSQLModel.metadata

config.set_main_option("sqlalchemy.url", build_database_url(DatabaseSettings(), hide_password=True))

_UNMANAGED_TABLES = {"apscheduler_jobs"}

_CONFIGURE_KWARGS: dict = {
    "target_metadata": target_metadata,
    "include_name": lambda name, type_, parent_names: name not in _UNMANAGED_TABLES if type_ == "table" else True,
}


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
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

"""Helpers for applying a PostgreSQL search_path (schema) on engine connections."""

from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from src.lib.utils.logging import logger


def _quote_schema(schema: str) -> str:
    """Double-quote a schema name for use in SQL.

    Schema names are validated by ``DatabaseSettings.validate_auth_requirements`` to match
    ``[a-zA-Z_][a-zA-Z0-9_]*``, so no embedded double-quotes are possible. The quoting is
    defensive and documents intent in ``SET search_path``.
    """
    return f'"{schema}"'


def _set_search_path_statement(schema: str) -> str:
    return f"SET search_path TO {_quote_schema(schema)}"


def attach_schema_listener(engine: Engine, schema: str) -> None:
    """Attach a ``connect`` event listener that sets ``search_path`` on every new DBAPI connection.

    Used by the synchronous engine builders (psycopg2 / pg8000) where the DBAPI connection is
    available to SQLAlchemy's ``connect`` event. For async engines with ``async_creator``, the
    search path is set inside the creator function instead, because the DBAPI-level ``connect``
    event does not fire for async-created connections.
    """

    statement = _set_search_path_statement(schema)

    @event.listens_for(engine, "connect")
    def _apply_search_path(dbapi_conn: Any, _connection_record: Any) -> None:
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute(statement)
        finally:
            cursor.close()

    logger.info("Attached search_path listener.", schema=schema)

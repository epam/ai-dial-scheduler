from unittest.mock import MagicMock

from sqlalchemy import create_engine

from src.lib.db import schema


class TestQuoteSchema:
    def test_simple_name_is_double_quoted(self):
        assert schema._quote_schema("myschema") == '"myschema"'

    def test_name_with_underscore_is_double_quoted(self):
        assert schema._quote_schema("my_schema") == '"my_schema"'


class TestSetSearchPathStatement:
    def test_builds_set_search_path_statement(self):
        stmt = schema._set_search_path_statement("myschema")
        assert stmt == 'SET search_path TO "myschema"'


class TestAttachSchemaListener:
    def test_attaches_listener_without_error(self):
        engine = create_engine("sqlite:///:memory:")

        # Should not raise
        schema.attach_schema_listener(engine, "myschema")

    def test_listener_executes_set_search_path_on_connect(self):
        engine = create_engine("sqlite:///:memory:")
        dbapi_conn = MagicMock()
        cursor = MagicMock()
        dbapi_conn.cursor.return_value = cursor

        # Capture the listener when it's registered
        registered_listeners = []

        def fake_listens_for(target, event_name):
            def decorator(func):
                registered_listeners.append((target, event_name, func))
                return func

            return decorator

        import sqlalchemy.event as sa_event

        original_listens_for = sa_event.listens_for
        sa_event.listens_for = fake_listens_for

        try:
            schema.attach_schema_listener(engine, "test_schema")

            assert len(registered_listeners) == 1
            _, event_name, listener_func = registered_listeners[0]
            assert event_name == "connect"

            listener_func(dbapi_conn, None)

            cursor.execute.assert_called_once_with('SET search_path TO "test_schema"')
            cursor.close.assert_called_once()
        finally:
            sa_event.listens_for = original_listens_for

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.lib.db import engine as engine_module
from src.lib.db.engine import Database
from src.lib.db.enums import DbAuthMode


@pytest.fixture
def fresh_database():
    Database._instance = None
    db = Database()
    yield db
    Database._instance = None


def _settings_with_mode(mode: DbAuthMode) -> SimpleNamespace:
    db = SimpleNamespace(
        db_auth_mode=mode,
        db_echo=False,
        db_pool_pre_ping=True,
        db_pool_size=10,
        db_max_overflow=20,
        db_pool_recycle_seconds=1800,
        db_pool_timeout_seconds=30,
        db_schema=None,
    )
    return SimpleNamespace(db=db)


@pytest.mark.parametrize("mode", [DbAuthMode.PASSWORD, DbAuthMode.CONNECTION_STRING])
def test_url_based_modes_use_url_engine(fresh_database, mocker, mode):
    mocker.patch.object(engine_module, "get_settings", return_value=_settings_with_mode(mode))
    url_based = mocker.patch.object(fresh_database, "_init_engine_url_based", return_value=MagicMock())
    gcp = mocker.patch.object(fresh_database, "_init_engine_gcp_iam", return_value=MagicMock())
    azure = mocker.patch.object(fresh_database, "_init_engine_azure_entra", return_value=MagicMock())

    fresh_database.init_engine(force_fresh=True)

    url_based.assert_called_once()
    gcp.assert_not_called()
    azure.assert_not_called()


def test_gcp_iam_mode_uses_gcp_engine(fresh_database, mocker):
    mocker.patch.object(engine_module, "get_settings", return_value=_settings_with_mode(DbAuthMode.GCP_IAM))
    url_based = mocker.patch.object(fresh_database, "_init_engine_url_based", return_value=MagicMock())
    gcp = mocker.patch.object(fresh_database, "_init_engine_gcp_iam", return_value=MagicMock())
    azure = mocker.patch.object(fresh_database, "_init_engine_azure_entra", return_value=MagicMock())

    fresh_database.init_engine(force_fresh=True)

    gcp.assert_called_once()
    url_based.assert_not_called()
    azure.assert_not_called()


def test_azure_entra_mode_uses_azure_engine(fresh_database, mocker):
    mocker.patch.object(engine_module, "get_settings", return_value=_settings_with_mode(DbAuthMode.AZURE_ENTRA))
    url_based = mocker.patch.object(fresh_database, "_init_engine_url_based", return_value=MagicMock())
    gcp = mocker.patch.object(fresh_database, "_init_engine_gcp_iam", return_value=MagicMock())
    azure = mocker.patch.object(fresh_database, "_init_engine_azure_entra", return_value=MagicMock())

    fresh_database.init_engine(force_fresh=True)

    azure.assert_called_once()
    url_based.assert_not_called()
    gcp.assert_not_called()


def _capture_async_creator(mocker, captured: dict):
    def _fake(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return MagicMock()

    mocker.patch.object(engine_module, "create_async_engine", side_effect=_fake)


async def test_gcp_getconn_invokes_connector_with_iam(fresh_database, mocker):
    settings = _settings_with_mode(DbAuthMode.GCP_IAM)
    settings.db.db_gcp_instance_connection_name = "proj:region:inst"
    settings.db.db_gcp_iam_user = "svc@proj.iam"
    settings.db.db_name = "scheddb"
    settings.db.db_gcp_private_ip = False
    mocker.patch.object(engine_module, "get_settings", return_value=settings)

    fake_connector = MagicMock()
    fake_connector.connect_async = mocker.AsyncMock(return_value="gcp-conn")
    mocker.patch.object(engine_module, "create_gcp_connector", return_value=fake_connector)
    captured: dict = {}
    _capture_async_creator(mocker, captured)

    fresh_database._init_engine_gcp_iam()
    result = await captured["async_creator"]()

    assert captured["url"] == "postgresql+asyncpg://"
    assert result == "gcp-conn"
    args, kwargs = fake_connector.connect_async.call_args
    assert args[0] == "proj:region:inst"
    assert args[1] == "asyncpg"
    assert kwargs["enable_iam_auth"] is True
    assert kwargs["user"] == "svc@proj.iam"
    assert kwargs["db"] == "scheddb"


async def test_azure_getconn_uses_fresh_token_as_password(fresh_database, mocker):
    settings = _settings_with_mode(DbAuthMode.AZURE_ENTRA)
    settings.db.db_host = "srv.postgres.database.azure.com"
    settings.db.db_port = 5432
    settings.db.db_azure_entra_user = "identity"
    settings.db.db_name = "scheddb"
    settings.db.db_azure_managed_identity_client_id = None
    mocker.patch.object(engine_module, "get_settings", return_value=settings)

    provider = MagicMock()
    provider.get_token_password = mocker.AsyncMock(return_value="fresh-token")
    mocker.patch.object(engine_module, "AsyncAzureTokenProvider", return_value=provider)
    connect_mock = mocker.AsyncMock(return_value="azure-conn")
    mocker.patch.object(engine_module.asyncpg, "connect", connect_mock)
    captured: dict = {}
    _capture_async_creator(mocker, captured)

    fresh_database._init_engine_azure_entra()
    result = await captured["async_creator"]()

    assert result == "azure-conn"
    _, kwargs = connect_mock.call_args
    assert kwargs["password"] == "fresh-token"
    assert kwargs["ssl"] == "require"
    assert kwargs["user"] == "identity"
    assert kwargs["host"] == "srv.postgres.database.azure.com"
    assert kwargs["database"] == "scheddb"


async def test_gcp_getconn_sets_search_path_when_schema_configured(fresh_database, mocker):
    settings = _settings_with_mode(DbAuthMode.GCP_IAM)
    settings.db.db_gcp_instance_connection_name = "proj:region:inst"
    settings.db.db_gcp_iam_user = "svc@proj.iam"
    settings.db.db_name = "scheddb"
    settings.db.db_gcp_private_ip = False
    settings.db.db_schema = "myschema"
    mocker.patch.object(engine_module, "get_settings", return_value=settings)

    fake_conn = MagicMock()
    fake_conn.execute = mocker.AsyncMock()
    fake_connector = MagicMock()
    fake_connector.connect_async = mocker.AsyncMock(return_value=fake_conn)
    mocker.patch.object(engine_module, "create_gcp_connector", return_value=fake_connector)
    captured: dict = {}
    _capture_async_creator(mocker, captured)

    fresh_database._init_engine_gcp_iam()
    await captured["async_creator"]()

    fake_conn.execute.assert_called_once_with('SET search_path TO "myschema"')


async def test_gcp_getconn_skips_search_path_when_schema_is_none(fresh_database, mocker):
    settings = _settings_with_mode(DbAuthMode.GCP_IAM)
    settings.db.db_gcp_instance_connection_name = "proj:region:inst"
    settings.db.db_gcp_iam_user = "svc@proj.iam"
    settings.db.db_name = "scheddb"
    settings.db.db_gcp_private_ip = False
    settings.db.db_schema = None
    mocker.patch.object(engine_module, "get_settings", return_value=settings)

    fake_conn = MagicMock()
    fake_conn.execute = mocker.AsyncMock()
    fake_connector = MagicMock()
    fake_connector.connect_async = mocker.AsyncMock(return_value=fake_conn)
    mocker.patch.object(engine_module, "create_gcp_connector", return_value=fake_connector)
    captured: dict = {}
    _capture_async_creator(mocker, captured)

    fresh_database._init_engine_gcp_iam()
    await captured["async_creator"]()

    fake_conn.execute.assert_not_called()


async def test_azure_getconn_sets_search_path_when_schema_configured(fresh_database, mocker):
    settings = _settings_with_mode(DbAuthMode.AZURE_ENTRA)
    settings.db.db_host = "srv.postgres.database.azure.com"
    settings.db.db_port = 5432
    settings.db.db_azure_entra_user = "identity"
    settings.db.db_name = "scheddb"
    settings.db.db_azure_managed_identity_client_id = None
    settings.db.db_schema = "myschema"
    mocker.patch.object(engine_module, "get_settings", return_value=settings)

    provider = MagicMock()
    provider.get_token_password = mocker.AsyncMock(return_value="fresh-token")
    mocker.patch.object(engine_module, "AsyncAzureTokenProvider", return_value=provider)

    fake_conn = MagicMock()
    fake_conn.execute = mocker.AsyncMock()
    connect_mock = mocker.AsyncMock(return_value=fake_conn)
    mocker.patch.object(engine_module.asyncpg, "connect", connect_mock)
    captured: dict = {}
    _capture_async_creator(mocker, captured)

    fresh_database._init_engine_azure_entra()
    await captured["async_creator"]()

    fake_conn.execute.assert_called_once_with('SET search_path TO "myschema"')


def test_url_based_mode_sets_server_settings_when_schema_configured(fresh_database, mocker):
    settings = _settings_with_mode(DbAuthMode.PASSWORD)
    settings.db.db_schema = "myschema"
    mocker.patch.object(engine_module, "get_settings", return_value=settings)
    mocker.patch.object(engine_module, "build_database_url", return_value="postgresql+asyncpg://u:p@localhost/db")
    captured: dict = {}

    def _fake_create_async_engine(url, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    mocker.patch.object(engine_module, "create_async_engine", side_effect=_fake_create_async_engine)

    fresh_database._init_engine_url_based()

    assert captured.get("connect_args") == {"server_settings": {"search_path": "myschema"}}


def test_url_based_mode_skips_server_settings_when_schema_is_none(fresh_database, mocker):
    settings = _settings_with_mode(DbAuthMode.PASSWORD)
    settings.db.db_schema = None
    mocker.patch.object(engine_module, "get_settings", return_value=settings)
    mocker.patch.object(engine_module, "build_database_url", return_value="postgresql+asyncpg://u:p@localhost/db")
    captured: dict = {}

    def _fake_create_async_engine(url, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    mocker.patch.object(engine_module, "create_async_engine", side_effect=_fake_create_async_engine)

    fresh_database._init_engine_url_based()

    assert "connect_args" not in captured


class TestReleaseCredentialsBestEffort:
    def test_gcp_connector_close_runs_with_timeout(self, fresh_database, mocker):
        connector = MagicMock()
        connector.close = MagicMock()
        fresh_database._gcp_connector = connector

        fresh_database._release_credentials_best_effort()

        # The close was attempted (may or may not have completed, depending on timing).
        assert fresh_database._gcp_connector is None

    def test_gcp_connector_close_timeout_does_not_block(self, fresh_database, mocker):
        import threading

        connector = MagicMock()
        # Simulate a hanging close
        close_event = threading.Event()
        connector.close = lambda: close_event.wait(timeout=10)
        fresh_database._gcp_connector = connector

        # This should return quickly (within ~3 seconds timeout)
        fresh_database._release_credentials_best_effort()

        assert fresh_database._gcp_connector is None
        close_event.set()  # release the hanging thread

    def test_azure_credential_closed_on_running_loop(self, fresh_database, mocker):
        provider = MagicMock()
        provider.close = mocker.AsyncMock()
        fresh_database._azure_credential = provider

        async def _run():
            fresh_database._release_credentials_best_effort()

        import asyncio

        asyncio.run(_run())

        assert fresh_database._azure_credential is None
        provider.close.assert_called_once()

    def test_azure_credential_close_timeout_does_not_block_without_loop(self, fresh_database, mocker):
        import asyncio

        async def _hanging_close() -> None:
            await asyncio.sleep(10)

        provider = MagicMock()
        provider.close = _hanging_close
        fresh_database._azure_credential = provider

        # This should return quickly (within ~3 seconds timeout), not hang for 10 seconds.
        fresh_database._release_credentials_best_effort()

        assert fresh_database._azure_credential is None

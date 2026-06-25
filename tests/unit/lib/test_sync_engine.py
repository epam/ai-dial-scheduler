from unittest.mock import MagicMock

import pytest

from src.lib.db import sync_engine
from src.lib.settings import DatabaseSettings


def _make_settings(monkeypatch, mode: str, **extra: str) -> DatabaseSettings:
    for name in (
        "DB_AUTH_MODE",
        "DB_PASSWORD",
        "DB_URL",
        "DB_SCHEMA",
        "DB_GCP_INSTANCE_CONNECTION_NAME",
        "DB_GCP_IAM_USER",
        "DB_GCP_PRIVATE_IP",
        "DB_AZURE_ENTRA_USER",
        "DB_AZURE_MANAGED_IDENTITY_CLIENT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DB_AUTH_MODE", mode)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    return DatabaseSettings(_env_file=None)


class TestUrlBasedSyncEngine:
    def test_password_mode_builds_psycopg2_engine(self, monkeypatch):
        settings = _make_settings(monkeypatch, "password", DB_PASSWORD="secret")
        engine = sync_engine.build_sync_engine(settings)
        assert str(engine.url).startswith("postgresql+psycopg2://")

    def test_connection_string_mode_builds_psycopg2_engine(self, monkeypatch):
        settings = _make_settings(monkeypatch, "connection-string", DB_URL="postgresql+asyncpg://u:p@localhost/db")
        engine = sync_engine.build_sync_engine(settings)
        assert str(engine.url).startswith("postgresql+psycopg2://")


class TestGcpSyncEngine:
    def test_builds_pg8000_engine_with_connector_creator(self, monkeypatch, mocker):
        settings = _make_settings(
            monkeypatch,
            "gcp_iam",
            DB_GCP_INSTANCE_CONNECTION_NAME="proj:region:inst",
            DB_GCP_IAM_USER="svc@proj.iam",
            DB_NAME="scheddb",
        )
        fake_connector = MagicMock()
        mocker.patch.object(sync_engine, "create_gcp_connector", return_value=fake_connector)
        mocker.patch.object(sync_engine, "event")  # prevent real event registration on MagicMock engine
        mock_create_engine = mocker.patch.object(sync_engine, "create_engine", return_value=MagicMock())

        sync_engine.build_sync_engine(settings)

        args, kwargs = mock_create_engine.call_args
        assert args[0] == "postgresql+pg8000://"
        creator = kwargs["creator"]
        creator()
        call_args, call_kwargs = fake_connector.connect.call_args
        assert call_args[0] == "proj:region:inst"
        assert call_args[1] == "pg8000"
        assert call_kwargs["enable_iam_auth"] is True
        assert call_kwargs["user"] == "svc@proj.iam"
        assert call_kwargs["db"] == "scheddb"

    def test_getconn_reraises_and_logs_on_failure(self, monkeypatch, mocker):
        settings = _make_settings(
            monkeypatch,
            "gcp_iam",
            DB_GCP_INSTANCE_CONNECTION_NAME="proj:region:inst",
            DB_GCP_IAM_USER="svc@proj.iam",
            DB_NAME="scheddb",
        )
        fake_connector = MagicMock()
        fake_connector.connect.side_effect = RuntimeError("iam error")
        mocker.patch.object(sync_engine, "create_gcp_connector", return_value=fake_connector)
        mocker.patch.object(sync_engine, "event")  # prevent real event registration on MagicMock engine
        mock_create_engine = mocker.patch.object(sync_engine, "create_engine", return_value=MagicMock())
        mock_logger = mocker.patch.object(sync_engine, "logger")

        sync_engine.build_sync_engine(settings)
        _, kwargs = mock_create_engine.call_args
        creator = kwargs["creator"]

        with pytest.raises(RuntimeError):
            creator()

        mock_logger.error.assert_called_once()
        call_kwargs = mock_logger.error.call_args.kwargs
        assert call_kwargs.get("mode") == "gcp_iam"
        assert "iam error" not in str(call_kwargs)


class TestAzureSyncEngine:
    def test_builds_passwordless_psycopg2_engine(self, monkeypatch, mocker):
        settings = _make_settings(
            monkeypatch,
            "azure_entra",
            DB_AZURE_ENTRA_USER="identity",
            DB_HOST="srv.postgres.database.azure.com",
            DB_NAME="scheddb",
        )
        mocker.patch.object(sync_engine, "AzureTokenProvider", return_value=MagicMock())

        engine = sync_engine.build_sync_engine(settings)

        url = str(engine.url)
        assert url.startswith("postgresql+psycopg2://")
        assert engine.url.username == "identity"
        assert engine.url.password in (None, "")

    def test_builds_engine_with_tls_required(self, monkeypatch, mocker):
        settings = _make_settings(
            monkeypatch,
            "azure_entra",
            DB_AZURE_ENTRA_USER="identity",
            DB_HOST="srv.postgres.database.azure.com",
            DB_NAME="scheddb",
        )
        mocker.patch.object(sync_engine, "AzureTokenProvider", return_value=MagicMock())
        mocker.patch.object(sync_engine, "event")  # prevent real event registration on MagicMock engine
        mock_create_engine = mocker.patch.object(sync_engine, "create_engine", return_value=MagicMock())

        sync_engine.build_sync_engine(settings)

        _, kwargs = mock_create_engine.call_args
        assert kwargs.get("connect_args", {}).get("sslmode") == "require"

    def test_token_injector_sets_fresh_password(self):
        provider = MagicMock()
        provider.get_token_password.return_value = "fresh-token"
        cparams: dict[str, object] = {}

        sync_engine._inject_azure_token(provider, cparams)

        assert cparams["password"] == "fresh-token"
        provider.get_token_password.assert_called_once()

    def test_token_injector_reraises_and_logs_on_failure(self, mocker):
        provider = MagicMock()
        provider.get_token_password.side_effect = RuntimeError("network failure")
        mock_logger = mocker.patch.object(sync_engine, "logger")
        cparams: dict[str, object] = {}

        with pytest.raises(RuntimeError):
            sync_engine._inject_azure_token(provider, cparams)

        mock_logger.error.assert_called_once()
        call_kwargs = mock_logger.error.call_args.kwargs
        assert call_kwargs.get("mode") == "azure_entra"
        assert "network failure" not in str(call_kwargs)


class TestUnsupportedMode:
    def test_unknown_mode_raises(self, monkeypatch):
        settings = _make_settings(monkeypatch, "password", DB_PASSWORD="x")
        object.__setattr__(settings, "db_auth_mode", "bogus")
        with pytest.raises(ValueError):
            sync_engine.build_sync_engine(settings)


class TestSchemaSupport:
    def test_schema_set_attaches_connect_event_listener(self, monkeypatch, mocker):
        settings = _make_settings(monkeypatch, "password", DB_PASSWORD="secret", DB_SCHEMA="myschema")
        mock_attach = mocker.patch.object(sync_engine, "attach_schema_listener")

        engine = sync_engine.build_sync_engine(settings)

        mock_attach.assert_called_once_with(engine, "myschema")

    def test_schema_unset_skips_listener(self, monkeypatch, mocker):
        settings = _make_settings(monkeypatch, "password", DB_PASSWORD="secret")
        mock_attach = mocker.patch.object(sync_engine, "attach_schema_listener")

        sync_engine.build_sync_engine(settings)

        mock_attach.assert_not_called()

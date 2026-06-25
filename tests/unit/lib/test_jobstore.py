from unittest.mock import MagicMock

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from src.lib.db import jobstore
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


class TestUrlBasedJobStore:
    def test_password_mode_uses_psycopg2_url(self, monkeypatch):
        settings = _make_settings(monkeypatch, "password", DB_PASSWORD="secret")
        store = jobstore.build_jobstore(settings)
        assert isinstance(store, SQLAlchemyJobStore)
        assert str(store.engine.url).startswith("postgresql+psycopg2://")

    def test_connection_string_mode_uses_psycopg2_url(self, monkeypatch):
        settings = _make_settings(monkeypatch, "connection-string", DB_URL="postgresql+asyncpg://u:p@localhost/db")
        store = jobstore.build_jobstore(settings)
        assert str(store.engine.url).startswith("postgresql+psycopg2://")


class TestWorkloadIdentityJobStore:
    def test_gcp_mode_passes_prebuilt_engine(self, monkeypatch, mocker):
        settings = _make_settings(
            monkeypatch,
            "gcp_iam",
            DB_GCP_INSTANCE_CONNECTION_NAME="proj:region:inst",
            DB_GCP_IAM_USER="svc@proj.iam",
        )
        sentinel = MagicMock()
        mocker.patch.object(jobstore, "build_sync_engine", return_value=sentinel)

        store = jobstore.build_jobstore(settings)

        assert store.engine is sentinel

    def test_azure_mode_passes_prebuilt_engine(self, monkeypatch, mocker):
        settings = _make_settings(monkeypatch, "azure_entra", DB_AZURE_ENTRA_USER="identity")
        sentinel = MagicMock()
        mocker.patch.object(jobstore, "build_sync_engine", return_value=sentinel)

        store = jobstore.build_jobstore(settings)

        assert store.engine is sentinel

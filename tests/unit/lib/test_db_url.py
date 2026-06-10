import pytest

from src.lib.db.enums import DbDriver
from src.lib.db.url import build_database_url
from src.lib.settings import Settings


@pytest.fixture
def password_settings(monkeypatch) -> Settings:
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
    monkeypatch.setenv("DIAL_API_KEY", "key")
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "testdb")
    monkeypatch.setenv("DB_USER", "testuser")
    monkeypatch.setenv("DB_PASSWORD", "testpass")
    monkeypatch.setenv("DB_AUTH_MODE", "password")
    return Settings()


@pytest.fixture
def connection_string_settings(monkeypatch) -> Settings:
    # Use env vars only (no explicit db= override) to exercise the real config flow
    monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
    monkeypatch.setenv("DIAL_API_KEY", "key")
    monkeypatch.setenv("DB_AUTH_MODE", "connection-string")
    monkeypatch.setenv("DB_URL", "postgresql+asyncpg://user:pass@localhost/db")
    return Settings()


class TestBuildDatabaseUrl:
    def test_password_async_returns_asyncpg_url(self, password_settings):
        url = build_database_url(password_settings.db)
        assert url.startswith("postgresql+asyncpg://")

    def test_sync_driver_override_returns_psycopg2_url(self, password_settings):
        url = build_database_url(password_settings.db, driver=DbDriver.SYNC)
        assert url.startswith("postgresql+psycopg2://")

    def test_sync_driver_overrides_settings_driver(self, password_settings):
        # Even if settings has ASYNC driver, passing SYNC should override
        assert password_settings.db.db_driver == DbDriver.ASYNC
        url = build_database_url(password_settings.db, driver=DbDriver.SYNC)
        assert url.startswith("postgresql+psycopg2://")

    def test_connection_string_mode_returns_url_verbatim(self, connection_string_settings):
        url = build_database_url(connection_string_settings.db)
        assert url == "postgresql+asyncpg://user:pass@localhost/db"

    def test_connection_string_mode_with_sync_driver_replaces_driver(self, connection_string_settings):
        # APScheduler requires a sync psycopg2 URL; the driver override must be honoured
        # even when a verbatim connection string is provided (plan.md constraint).
        url = build_database_url(connection_string_settings.db, driver=DbDriver.SYNC)
        assert url.startswith("postgresql+psycopg2://")
        assert "asyncpg" not in url

    def test_hide_password_omits_password_value(self, password_settings):
        url = build_database_url(password_settings.db, hide_password=True)
        assert "testpass" not in url

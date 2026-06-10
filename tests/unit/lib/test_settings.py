import pytest

from src.lib.db.enums import DbAuthMode, DbDriver
from src.lib.settings import DatabaseSettings, Settings, get_settings


class TestDatabaseSettings:
    def test_password_mode_requires_db_password(self):
        with pytest.raises(ValueError, match="db_password"):
            DatabaseSettings(db_auth_mode=DbAuthMode.PASSWORD, db_password=None)

    def test_connection_string_mode_requires_db_url(self):
        with pytest.raises(ValueError, match="db_url"):
            DatabaseSettings(db_auth_mode=DbAuthMode.CONNECTION_STRING, db_url=None)

    def test_password_mode_valid(self):
        s = DatabaseSettings(db_auth_mode=DbAuthMode.PASSWORD, db_password="secret")
        assert s.db_password == "secret"
        assert s.db_auth_mode == DbAuthMode.PASSWORD

    def test_connection_string_mode_valid(self):
        s = DatabaseSettings(
            db_auth_mode=DbAuthMode.CONNECTION_STRING,
            db_url="postgresql+asyncpg://user:pass@localhost/db",
        )
        assert s.db_url == "postgresql+asyncpg://user:pass@localhost/db"

    def test_default_driver_is_async(self):
        s = DatabaseSettings(db_auth_mode=DbAuthMode.PASSWORD, db_password="x")
        assert s.db_driver == DbDriver.ASYNC


class TestSettings:
    def test_populates_all_fields_from_env(self, monkeypatch):
        monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
        monkeypatch.setenv("DIAL_API_KEY", "test-key")
        monkeypatch.setenv("APP_NAME", "my-scheduler")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("PORT", "8080")
        monkeypatch.setenv("DB_PASSWORD", "dbpass")

        s = Settings()

        assert s.dial_url == "https://dial.example.com"
        assert s.dial_api_key.get_secret_value() == "test-key"
        assert s.app_name == "my-scheduler"
        assert s.env == "production"
        assert s.port == 8080


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestGetSettings:
    def test_returns_same_cached_instance(self, monkeypatch):
        monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
        monkeypatch.setenv("DIAL_API_KEY", "key")
        monkeypatch.setenv("DB_PASSWORD", "pass")

        s1 = get_settings()
        s2 = get_settings()

        assert s1 is s2

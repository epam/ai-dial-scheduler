import pytest

from src.lib.db.enums import DbAuthMode, DbDriver
from src.lib.settings import ApiHttpSettings, DatabaseSettings, Settings, WorkerHttpSettings, get_settings


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


class TestUserIdPathList:
    def test_default_user_id_path_list(self, monkeypatch):
        monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
        monkeypatch.setenv("DIAL_API_KEY", "key")
        monkeypatch.setenv("DB_PASSWORD", "pass")

        s = Settings()

        assert s.user_id_path_list == ["sub", "userClaims.sub.0", "project"]

    def test_user_id_path_list_override(self, monkeypatch):
        monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
        monkeypatch.setenv("DIAL_API_KEY", "key")
        monkeypatch.setenv("DB_PASSWORD", "pass")
        monkeypatch.setenv("USER_ID_PATH_LIST", '["project", "sub"]')

        s = Settings()

        assert s.user_id_path_list == ["project", "sub"]


class TestSchedulerAppId:
    def test_default_scheduler_app_id(self, monkeypatch):
        monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
        monkeypatch.setenv("DIAL_API_KEY", "key")
        monkeypatch.setenv("DB_PASSWORD", "pass")

        s = Settings()

        assert s.scheduler_app_id == "dial_scheduler"

    def test_scheduler_app_id_override(self, monkeypatch):
        monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
        monkeypatch.setenv("DIAL_API_KEY", "key")
        monkeypatch.setenv("DB_PASSWORD", "pass")
        monkeypatch.setenv("SCHEDULER_APP_ID", "custom_app")

        s = Settings()

        assert s.scheduler_app_id == "custom_app"


class TestWorkerSettings:
    def test_noop_interval_seconds_default(self, monkeypatch):
        monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
        monkeypatch.setenv("DIAL_API_KEY", "key")
        monkeypatch.setenv("DB_PASSWORD", "pass")

        s = Settings()

        assert s.noop_interval_seconds == 60

    def test_noop_interval_seconds_override(self, monkeypatch):
        monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
        monkeypatch.setenv("DIAL_API_KEY", "key")
        monkeypatch.setenv("DB_PASSWORD", "pass")
        monkeypatch.setenv("NOOP_INTERVAL_SECONDS", "120")

        s = Settings()

        assert s.noop_interval_seconds == 120

    def test_worker_health_settings_defaults(self, monkeypatch):
        monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
        monkeypatch.setenv("DIAL_API_KEY", "key")
        monkeypatch.setenv("DB_PASSWORD", "pass")

        s = Settings()

        assert s.worker_health_dir == "/tmp"
        assert s.worker_liveness_staleness_seconds == 180.0
        assert s.worker_readiness_check_interval_seconds == 15.0

    def test_worker_health_settings_override(self, monkeypatch):
        monkeypatch.setenv("DIAL_URL", "https://dial.example.com")
        monkeypatch.setenv("DIAL_API_KEY", "key")
        monkeypatch.setenv("DB_PASSWORD", "pass")
        monkeypatch.setenv("WORKER_HEALTH_DIR", "/var/run/worker-health")
        monkeypatch.setenv("WORKER_LIVENESS_STALENESS_SECONDS", "90")
        monkeypatch.setenv("WORKER_READINESS_CHECK_INTERVAL_SECONDS", "5")

        s = Settings()

        assert s.worker_health_dir == "/var/run/worker-health"
        assert s.worker_liveness_staleness_seconds == 90.0
        assert s.worker_readiness_check_interval_seconds == 5.0


class TestApiHttpSettings:
    def test_defaults(self):
        s = ApiHttpSettings()

        assert s.timeout_seconds == 60.0
        assert s.connect_timeout_seconds == 5.0
        assert s.read_timeout_seconds == 60.0
        assert s.write_timeout_seconds == 60.0
        assert s.pool_timeout_seconds == 5.0
        assert s.max_connections == 200
        assert s.max_keepalive_connections == 20

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("API_HTTP_TIMEOUT_SECONDS", "120.0")
        monkeypatch.setenv("API_HTTP_CONNECT_TIMEOUT_SECONDS", "10.0")

        s = ApiHttpSettings()

        assert s.timeout_seconds == 120.0
        assert s.connect_timeout_seconds == 10.0


class TestWorkerHttpSettings:
    def test_defaults(self):
        s = WorkerHttpSettings()

        assert s.timeout_seconds == 60.0
        assert s.connect_timeout_seconds == 5.0
        assert s.read_timeout_seconds == 60.0
        assert s.write_timeout_seconds == 60.0
        assert s.pool_timeout_seconds == 5.0
        assert s.max_connections == 200
        assert s.max_keepalive_connections == 20

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("WORKER_HTTP_TIMEOUT_SECONDS", "45.0")
        monkeypatch.setenv("WORKER_HTTP_READ_TIMEOUT_SECONDS", "90.0")

        s = WorkerHttpSettings()

        assert s.timeout_seconds == 45.0
        assert s.read_timeout_seconds == 90.0

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytest

from src.lib.db.enums import DbAuthMode
from src.lib.settings import DatabaseSettings, Settings
from src.worker.app import create_worker


@pytest.fixture
def mock_settings(mocker) -> Settings:
    db_settings = DatabaseSettings(
        db_auth_mode=DbAuthMode.PASSWORD,
        db_password="testpass",
        db_host="localhost",
        db_port=5432,
        db_name="testdb",
        db_user="testuser",
    )
    settings = mocker.MagicMock(spec=Settings)
    settings.dial_url = "https://dial.example.com"
    settings.app_name = "aidial-scheduler"
    settings.db = db_settings
    settings.noop_interval_seconds = 30
    settings.job_coalesce = False
    settings.job_max_instances = 100
    settings.job_misfire_grace_time = 60
    return settings


@pytest.fixture
def patched_create_worker(mocker, mock_settings):
    mocker.patch("src.worker.app.get_settings", return_value=mock_settings)
    mocker.patch("src.worker.app.build_jobstore", return_value=mocker.MagicMock(spec=SQLAlchemyJobStore))
    return mock_settings


class TestCreateWorker:
    def test_returns_async_io_scheduler(self, patched_create_worker):
        scheduler = create_worker()
        assert isinstance(scheduler, AsyncIOScheduler)

    def test_default_jobstore_is_sqlalchemy(self, patched_create_worker, mocker):
        scheduler = create_worker()
        assert "default" in scheduler._jobstores

    def test_only_default_jobstore_exists(self, patched_create_worker):
        scheduler = create_worker()
        assert list(scheduler._jobstores.keys()) == ["default"]

    def test_timezone_is_utc(self, patched_create_worker):
        scheduler = create_worker()
        assert str(scheduler.timezone) == "UTC"

    def test_job_defaults_read_from_settings(self, patched_create_worker):
        scheduler = create_worker()
        assert scheduler._job_defaults.get("coalesce") == patched_create_worker.job_coalesce
        assert scheduler._job_defaults.get("max_instances") == patched_create_worker.job_max_instances
        assert scheduler._job_defaults.get("misfire_grace_time") == patched_create_worker.job_misfire_grace_time


class TestHeartbeatJobRegistration:
    def test_default_store_is_sqlalchemy(self, patched_create_worker):
        scheduler = create_worker()
        assert "default" in scheduler._jobstores
        assert isinstance(scheduler._jobstores["default"], SQLAlchemyJobStore)

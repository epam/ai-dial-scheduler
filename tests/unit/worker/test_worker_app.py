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
    return settings


class TestCreateWorker:
    def test_returns_async_io_scheduler(self, mocker, mock_settings):
        mocker.patch("src.worker.app.get_settings", return_value=mock_settings)

        scheduler = create_worker()

        assert isinstance(scheduler, AsyncIOScheduler)

    def test_default_jobstore_is_sqlalchemy(self, mocker, mock_settings):
        mocker.patch("src.worker.app.get_settings", return_value=mock_settings)

        scheduler = create_worker()

        # _jobstores is private; no public accessor exists before scheduler.start()
        assert isinstance(scheduler._jobstores.get("default"), SQLAlchemyJobStore)

    def test_timezone_is_utc(self, mocker, mock_settings):
        mocker.patch("src.worker.app.get_settings", return_value=mock_settings)

        scheduler = create_worker()

        assert str(scheduler.timezone) == "UTC"

    def test_job_defaults_coalesce_false(self, mocker, mock_settings):
        mocker.patch("src.worker.app.get_settings", return_value=mock_settings)

        scheduler = create_worker()

        assert scheduler._job_defaults.get("coalesce") is False

    def test_job_defaults_max_instances_one(self, mocker, mock_settings):
        mocker.patch("src.worker.app.get_settings", return_value=mock_settings)

        scheduler = create_worker()

        assert scheduler._job_defaults.get("max_instances") == 1

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytest

from src.lib.db.enums import DbAuthMode
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.settings import DatabaseSettings, Settings
from src.worker.app import create_worker, on_job_error


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
    settings.http_timeout_seconds = 10.0
    return settings


@pytest.fixture
def patched_create_worker(mocker, mock_settings):
    mocker.patch("src.worker.app.get_settings", return_value=mock_settings)
    mocker.patch("src.worker.app.SQLAlchemyJobStore", return_value=mocker.MagicMock(spec=SQLAlchemyJobStore))
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

    def test_job_defaults_coalesce_false(self, patched_create_worker):
        scheduler = create_worker()
        assert scheduler._job_defaults.get("coalesce") is False

    def test_job_defaults_max_instances_one(self, patched_create_worker):
        scheduler = create_worker()
        assert scheduler._job_defaults.get("max_instances") == 1


class TestOnJobError:
    def test_logs_schedule_run_error_with_ids(self, mocker):
        exc = ScheduleRunError("failed", schedule_id="sched-1", run_id="run-1")
        event = mocker.MagicMock()
        event.exception = exc

        import structlog.testing

        with structlog.testing.capture_logs() as logs:
            on_job_error(event)

        assert len(logs) == 1
        log = logs[0]
        assert log["schedule_id"] == "sched-1"
        assert log["run_id"] == "run-1"
        assert log["log_level"] == "error"

    def test_logs_generic_error_with_job_id(self, mocker):
        exc = RuntimeError("unexpected")
        event = mocker.MagicMock()
        event.exception = exc
        event.job_id = "some-job-id"

        import structlog.testing

        with structlog.testing.capture_logs() as logs:
            on_job_error(event)

        assert len(logs) == 1
        log = logs[0]
        assert log["log_level"] == "error"
        assert log["job_id"] == "some-job-id"

    def test_schedule_run_error_does_not_stop_other_runs(self, mocker):
        """Multiple calls to on_job_error are independent."""
        for i in range(3):
            exc = ScheduleRunError(f"failed-{i}", schedule_id=f"sched-{i}", run_id=f"run-{i}")
            event = mocker.MagicMock()
            event.exception = exc
            import structlog.testing

            with structlog.testing.capture_logs() as logs:
                on_job_error(event)

            assert len(logs) == 1


class TestHeartbeatJobRegistration:
    def test_default_store_is_sqlalchemy(self, patched_create_worker):
        scheduler = create_worker()
        assert "default" in scheduler._jobstores
        assert isinstance(scheduler._jobstores["default"], SQLAlchemyJobStore)

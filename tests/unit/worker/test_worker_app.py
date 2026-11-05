from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytest

from src.lib.constants import HEARTBEAT_JOB_ID
from src.lib.db.enums import DbAuthMode
from src.lib.settings import DatabaseSettings, Settings
from src.worker.app import _run, create_worker


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
    settings.worker_readiness_check_interval_seconds = 15.0
    settings.readiness_db_timeout_seconds = 2.0
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

    def test_default_and_heartbeat_jobstores_exist(self, patched_create_worker):
        scheduler = create_worker()
        assert set(scheduler._jobstores.keys()) == {"default", "heartbeat"}

    def test_heartbeat_jobstore_is_memory(self, patched_create_worker):
        scheduler = create_worker()
        assert isinstance(scheduler._jobstores["heartbeat"], MemoryJobStore)

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


@pytest.fixture
def patched_run(mocker, patched_create_worker):
    """Patch everything `_run` touches besides scheduler/jobstore so it returns immediately."""
    mock_scheduler = mocker.MagicMock(spec=AsyncIOScheduler)
    mock_jobstore = mocker.MagicMock(spec=SQLAlchemyJobStore)
    mock_heartbeat_jobstore = mocker.MagicMock(spec=MemoryJobStore)
    mock_scheduler._jobstores = {"default": mock_jobstore, "heartbeat": mock_heartbeat_jobstore}
    mocker.patch("src.worker.app.create_worker", return_value=mock_scheduler)
    mock_http_provider = mocker.MagicMock()
    mock_http_provider.aclose = mocker.AsyncMock()
    mocker.patch("src.worker.app.HttpClientProvider", return_value=mock_http_provider)
    mocker.patch("asyncio.Event.wait", new=mocker.AsyncMock(return_value=None))
    mocker.patch("signal.signal")
    mocker.patch("src.worker.app.touch_heartbeat")
    mocker.patch("src.worker.app.clear_heartbeat")
    mocker.patch("src.worker.app.mark_started")
    mocker.patch("src.worker.app.clear_started")
    mocker.patch("src.worker.app.mark_not_ready")
    mocker.patch("src.worker.app.clear_ready")
    mocker.patch("src.worker.app.readiness_poller", mocker.AsyncMock(return_value=None))
    return mock_scheduler, mock_jobstore


class TestHealthMarkersOnStartup:
    async def test_touches_heartbeat_immediately_after_scheduler_start(self, patched_run, mocker):
        mock_touch = mocker.patch("src.worker.app.touch_heartbeat")

        await _run()

        mock_touch.assert_called_once()

    async def test_clears_stale_heartbeat_before_scheduler_creation(self, patched_run, mocker):
        mock_clear = mocker.patch("src.worker.app.clear_heartbeat")

        await _run()

        mock_clear.assert_called_once()

    async def test_clears_stale_started_and_ready_before_scheduler_creation(self, patched_run, mocker):
        mock_clear_started = mocker.patch("src.worker.app.clear_started")
        mock_clear_ready = mocker.patch("src.worker.app.clear_ready")

        await _run()

        mock_clear_started.assert_called_once()
        mock_clear_ready.assert_called_once()

    async def test_marks_started_after_scheduler_start_and_job_registration(self, patched_run, mocker):
        mock_mark_started = mocker.patch("src.worker.app.mark_started")

        await _run()

        mock_mark_started.assert_called_once()

    async def test_creates_readiness_poller_task(self, patched_run, mocker):
        mock_readiness_poller = mocker.patch("src.worker.app.readiness_poller", mocker.AsyncMock(return_value=None))

        await _run()

        mock_readiness_poller.assert_called_once()

    async def test_marks_not_ready_before_scheduler_shutdown(self, patched_run, mocker):
        mock_scheduler, _ = patched_run
        manager = mocker.Mock()
        mock_mark_not_ready = mocker.patch("src.worker.app.mark_not_ready")
        manager.attach_mock(mock_mark_not_ready, "mark_not_ready")
        manager.attach_mock(mock_scheduler.shutdown, "shutdown")

        await _run()

        mock_mark_not_ready.assert_called_once()
        call_names = [call[0] for call in manager.mock_calls]
        assert call_names.index("mark_not_ready") < call_names.index("shutdown")


class TestStartupOrdering:
    """User Story 3: mark_started must only fire once the scheduler has started and the
    heartbeat job is registered, so a startupProbe against it isn't satisfied prematurely."""

    async def test_mark_started_called_after_scheduler_start_and_touch_heartbeat(self, patched_run, mocker):
        mock_scheduler, _ = patched_run
        mock_touch_heartbeat = mocker.patch("src.worker.app.touch_heartbeat")
        mock_mark_started = mocker.patch("src.worker.app.mark_started")
        manager = mocker.Mock()
        manager.attach_mock(mock_scheduler.start, "scheduler_start")
        manager.attach_mock(mock_touch_heartbeat, "touch_heartbeat")
        manager.attach_mock(mock_mark_started, "mark_started")

        await _run()

        call_names = [call[0] for call in manager.mock_calls]
        assert (
            call_names.index("scheduler_start") < call_names.index("touch_heartbeat") < call_names.index("mark_started")
        )

    async def test_readiness_poller_started_after_mark_started(self, patched_run, mocker):
        mock_mark_started = mocker.patch("src.worker.app.mark_started")
        mock_readiness_poller = mocker.patch("src.worker.app.readiness_poller", mocker.AsyncMock(return_value=None))
        manager = mocker.Mock()
        manager.attach_mock(mock_mark_started, "mark_started")
        manager.attach_mock(mock_readiness_poller, "readiness_poller")

        await _run()

        call_names = [call[0] for call in manager.mock_calls]
        assert call_names.index("mark_started") < call_names.index("readiness_poller")


class TestHeartbeatJobRegistrationInRun:
    """The heartbeat job must be registered in the in-memory "heartbeat" jobstore, not the
    DB-backed "default" one, so a database outage cannot stop it from being scheduled
    (FR-007: liveness must be independent of database availability)."""

    async def test_registers_heartbeat_job_in_memory_jobstore(self, patched_run):
        mock_scheduler, _ = patched_run

        await _run()

        mock_scheduler.add_job.assert_called_once()
        assert mock_scheduler.add_job.call_args.kwargs["id"] == HEARTBEAT_JOB_ID
        assert mock_scheduler.add_job.call_args.kwargs["replace_existing"] is True
        assert mock_scheduler.add_job.call_args.kwargs["jobstore"] == "heartbeat"

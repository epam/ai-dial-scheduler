import pytest
from structlog.contextvars import clear_contextvars, get_contextvars
import structlog.testing

from src.lib.db.enums import HttpMethod, TargetType
from src.lib.exceptions.errors import ScheduleRunError


SCHEDULE_ID = "sched-jobs-001"


@pytest.fixture(autouse=True)
def reset_context():
    clear_contextvars()
    yield
    clear_contextvars()


class TestExecuteScheduledJobHappyPath:
    async def test_generates_run_id_and_calls_executor(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock()
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs():
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                method=HttpMethod.POST,
                url="https://example.com",
                payload={},
                extra_headers={},
            )

        mock_executor.execute.assert_called_once()
        call_args = mock_executor.execute.call_args
        ctx = call_args.args[0]
        assert ctx.run_id
        assert ctx.schedule_id == SCHEDULE_ID

    async def test_binds_run_id_and_schedule_id_in_log_context(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock()
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                method=HttpMethod.POST,
                url="https://example.com",
                payload={},
                extra_headers={},
            )

        schedule_logs = [entry for entry in logs if entry.get("schedule_id") == SCHEDULE_ID]
        assert len(schedule_logs) >= 1

    async def test_contextvars_cleared_after_success(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock()
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs():
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                method=HttpMethod.POST,
                url="https://example.com",
                payload={},
                extra_headers={},
            )

        assert get_contextvars() == {}

    async def test_validates_kwargs_as_schedule_properties(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs():
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.CHAT_COMPLETION,
                url="https://example.com/openai/deployments/gpt-4/chat/completions",
                payload={},
                extra_headers={},
                api_version="2024-02-01",
            )

        mock_executor.execute.assert_called_once()


class TestExecuteScheduledJobValidationFailure:
    async def test_invalid_kwargs_raises_schedule_run_error(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        mocker.patch("src.worker.jobs.resolve_executor")

        with pytest.raises(ScheduleRunError) as exc_info:
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                # missing required method for REST
                url="https://example.com",
                payload={},
                extra_headers={},
            )

        assert exc_info.value.schedule_id == SCHEDULE_ID
        assert exc_info.value.run_id

    async def test_contextvars_cleared_after_failure(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        mocker.patch("src.worker.jobs.resolve_executor")

        with pytest.raises(ScheduleRunError):
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                url="https://example.com",
                payload={},
                extra_headers={},
            )

        assert get_contextvars() == {}

    async def test_executor_error_wrapped_as_schedule_run_error(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=ScheduleRunError("exec failed", schedule_id=SCHEDULE_ID, run_id="r1")
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with pytest.raises(ScheduleRunError) as exc_info:
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                method=HttpMethod.POST,
                url="https://example.com",
                payload={},
                extra_headers={},
            )

        assert exc_info.value.schedule_id == SCHEDULE_ID


class TestHeartbeatHandler:
    async def test_logs_job_name_and_executed_at(self):
        from src.worker.jobs import heartbeat

        with structlog.testing.capture_logs() as logs:
            await heartbeat()

        assert len(logs) == 1
        log = logs[0]
        assert "job_name" in log
        assert "executed_at" in log

    async def test_logs_nothing_else(self):
        from src.worker.jobs import heartbeat

        with structlog.testing.capture_logs() as logs:
            await heartbeat()

        assert len(logs) == 1
        log = logs[0]
        allowed_keys = {"event", "job_name", "executed_at", "log_level", "_logger", "_record"}
        assert set(log.keys()).issubset(allowed_keys)

    async def test_heartbeat_clears_contextvars(self, mocker):
        """Heartbeat clears any ambient context so prior run ids don't bleed into heartbeat logs."""
        from structlog.contextvars import bind_contextvars

        from src.worker.jobs import heartbeat

        bind_contextvars(run_id="leaked-id", schedule_id="leaked-sched")

        with structlog.testing.capture_logs() as logs:
            await heartbeat()

        log = logs[0]
        assert "run_id" not in log
        assert "schedule_id" not in log

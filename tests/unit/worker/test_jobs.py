import uuid

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


@pytest.fixture
def mock_run_repo(mocker):
    import uuid

    mock_repo = mocker.AsyncMock()
    created_row = mocker.MagicMock()
    created_row.id = uuid.uuid4()
    mock_repo.create_in_progress = mocker.AsyncMock(return_value=created_row)
    mock_repo.mark_success = mocker.AsyncMock(return_value=mocker.MagicMock())
    mock_repo.mark_error = mocker.AsyncMock(return_value=mocker.MagicMock())
    mocker.patch("src.worker.jobs.ScheduleRunRepository", return_value=mock_repo)
    mocker.patch("src.worker.jobs.Database")
    return mock_repo


class TestExecuteScheduledJobHappyPath:
    async def test_generates_run_id_and_calls_executor(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
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

    async def test_binds_run_id_and_schedule_id_in_log_context(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
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

    async def test_contextvars_cleared_after_success(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
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

    async def test_validates_kwargs_as_schedule_properties(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"result": {"id": "cmpl-1", "choices": []}})
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
    async def test_invalid_kwargs_raises_schedule_run_error(self, mocker, mock_run_repo):
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

    async def test_contextvars_cleared_after_failure(self, mocker, mock_run_repo):
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

    async def test_executor_error_wrapped_as_schedule_run_error(self, mocker, mock_run_repo):
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


class TestExecuteScheduledJobInProgressCreation:
    async def test_creates_in_progress_run_before_execution(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        created_row = mocker.MagicMock()
        created_row.id = uuid.uuid4()
        mock_repo = mocker.AsyncMock()
        mock_repo.create_in_progress = mocker.AsyncMock(return_value=created_row)
        mock_repo.mark_success = mocker.AsyncMock(return_value=mocker.MagicMock())
        mock_repo.mark_error = mocker.AsyncMock(return_value=mocker.MagicMock())
        mocker.patch(
            "src.worker.jobs.ScheduleRunRepository",
            return_value=mock_repo,
        )
        mocker.patch("src.worker.jobs.Database")
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
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

        mock_repo.create_in_progress.assert_awaited_once()
        kwargs = mock_repo.create_in_progress.call_args.kwargs
        assert kwargs["schedule_id"] == SCHEDULE_ID
        assert isinstance(kwargs["schedule_snapshot"], dict)

    async def test_schedule_snapshot_contains_kwargs(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        created_row = mocker.MagicMock()
        created_row.id = uuid.uuid4()
        mock_repo = mocker.AsyncMock()
        mock_repo.create_in_progress = mocker.AsyncMock(return_value=created_row)
        mock_repo.mark_success = mocker.AsyncMock(return_value=mocker.MagicMock())
        mocker.patch("src.worker.jobs.ScheduleRunRepository", return_value=mock_repo)
        mocker.patch("src.worker.jobs.Database")
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs():
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                method=HttpMethod.POST,
                url="https://example.com/test",
                payload={"key": "val"},
                extra_headers={},
            )

        snapshot = mock_repo.create_in_progress.call_args.kwargs["schedule_snapshot"]
        assert snapshot["url"] == "https://example.com/test"
        assert snapshot["target_type"] == "rest"

    async def test_in_progress_created_before_validation(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        created_row = mocker.MagicMock()
        created_row.id = uuid.uuid4()
        mock_repo = mocker.AsyncMock()
        mock_repo.create_in_progress = mocker.AsyncMock(return_value=created_row)
        mock_repo.mark_error = mocker.AsyncMock(return_value=mocker.MagicMock())
        mocker.patch("src.worker.jobs.ScheduleRunRepository", return_value=mock_repo)
        mocker.patch("src.worker.jobs.Database")
        mocker.patch("src.worker.jobs.resolve_executor")

        with pytest.raises(ScheduleRunError):
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                # missing method — will fail validation
                url="https://example.com",
                payload={},
                extra_headers={},
            )

        mock_repo.create_in_progress.assert_awaited_once()

    async def test_create_in_progress_failure_aborts_run(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        mock_repo = mocker.AsyncMock()
        mock_repo.create_in_progress = mocker.AsyncMock(side_effect=Exception("DB down"))
        mocker.patch("src.worker.jobs.ScheduleRunRepository", return_value=mock_repo)
        mocker.patch("src.worker.jobs.Database")
        mock_executor = mocker.AsyncMock()
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

        mock_executor.execute.assert_not_awaited()
        assert any(log.get("event") == "run_create_failed" and log.get("log_level") == "error" for log in logs)

    async def test_create_in_progress_failure_clears_contextvars(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        mock_repo = mocker.AsyncMock()
        mock_repo.create_in_progress = mocker.AsyncMock(side_effect=Exception("DB down"))
        mocker.patch("src.worker.jobs.ScheduleRunRepository", return_value=mock_repo)
        mocker.patch("src.worker.jobs.Database")
        mocker.patch("src.worker.jobs.resolve_executor")

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

    async def test_logs_run_created(self, mocker):
        from src.worker.jobs import execute_scheduled_job

        created_row = mocker.MagicMock()
        created_row.id = uuid.uuid4()
        mock_repo = mocker.AsyncMock()
        mock_repo.create_in_progress = mocker.AsyncMock(return_value=created_row)
        mock_repo.mark_success = mocker.AsyncMock(return_value=mocker.MagicMock())
        mocker.patch("src.worker.jobs.ScheduleRunRepository", return_value=mock_repo)
        mocker.patch("src.worker.jobs.Database")
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
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

        assert any(log.get("event") == "run_created" for log in logs)


class TestExecuteScheduledJobPersistence:
    async def test_marks_success_after_execution(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        exec_result = {"status_code": 200, "body": {"ok": True}}
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value=exec_result)
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

        mock_run_repo.mark_success.assert_awaited_once()
        call_args = mock_run_repo.mark_success.call_args
        assert call_args.args[1] == exec_result

    async def test_marks_error_on_schedule_run_error(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=ScheduleRunError("timeout", schedule_id=SCHEDULE_ID, run_id="run-x")
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs():
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(
                    schedule_id=SCHEDULE_ID,
                    target_type=TargetType.REST,
                    method=HttpMethod.POST,
                    url="https://example.com",
                    payload={},
                    extra_headers={},
                )

        mock_run_repo.mark_error.assert_awaited_once()

    async def test_marks_error_on_validation_failure(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mocker.patch("src.worker.jobs.resolve_executor")

        with structlog.testing.capture_logs():
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(
                    schedule_id=SCHEDULE_ID,
                    target_type=TargetType.REST,
                    url="https://example.com",
                    payload={},
                    extra_headers={},
                )

        mock_run_repo.mark_error.assert_awaited_once()

    async def test_logs_run_recorded_success(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
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

        assert any(log.get("event") == "run_recorded_success" for log in logs)

    async def test_logs_run_recorded_error(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=ScheduleRunError("fail", schedule_id=SCHEDULE_ID, run_id="run-x")
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(
                    schedule_id=SCHEDULE_ID,
                    target_type=TargetType.REST,
                    method=HttpMethod.POST,
                    url="https://example.com",
                    payload={},
                    extra_headers={},
                )

        assert any(log.get("event") == "run_recorded_error" for log in logs)

    async def test_logs_run_failed_on_error(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=ScheduleRunError("fail", schedule_id=SCHEDULE_ID, run_id="run-x")
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(
                    schedule_id=SCHEDULE_ID,
                    target_type=TargetType.REST,
                    method=HttpMethod.POST,
                    url="https://example.com",
                    payload={},
                    extra_headers={},
                )

        assert any(log.get("event") == "run_failed" for log in logs)

    async def test_logs_run_persist_failed_when_mark_success_raises(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_run_repo.mark_success = mocker.AsyncMock(side_effect=Exception("DB gone"))
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
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

        assert any(log.get("event") == "run_persist_failed" for log in logs)

    async def test_logs_run_record_missing_when_mark_success_returns_none(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_run_repo.mark_success = mocker.AsyncMock(return_value=None)
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
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

        assert any(log.get("event") == "run_record_missing" for log in logs)


class TestExecuteScheduledJobTimeoutAndRetry:
    @pytest.fixture
    def no_sleep(self, mocker):
        return mocker.patch("src.worker.retry.asyncio.sleep", new=mocker.AsyncMock())

    async def test_success_persists_plain_result_without_attempts(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        exec_result = {"status_code": 200, "body": {}}
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value=exec_result)
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

        # mark_success receives only (run_id, result) — no attempt history persisted.
        call = mock_run_repo.mark_success.call_args
        assert call.args[1] == exec_result
        assert len(call.args) == 2

    async def test_retry_then_success(self, mocker, mock_run_repo, no_sleep):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=[
                ScheduleRunError("500", schedule_id=SCHEDULE_ID, run_id="r", status_code=500),
                {"status_code": 200, "body": {}},
            ]
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                method=HttpMethod.POST,
                url="https://example.com",
                payload={},
                extra_headers={},
                retry={"interval_sequence": [2], "retryable_errors": [500]},
            )

        assert mock_executor.execute.await_count == 2
        mock_run_repo.mark_success.assert_awaited_once()
        # Attempts are reflected in logs, not the DB.
        assert any(log.get("event") == "retry_scheduled" for log in logs)
        assert any(log.get("event") == "run_complete" and log.get("attempts") == 2 for log in logs)

    async def test_exhausted_retries_marks_error_and_raises(self, mocker, mock_run_repo, no_sleep):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=ScheduleRunError("500", schedule_id=SCHEDULE_ID, run_id="r", status_code=500)
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(
                    schedule_id=SCHEDULE_ID,
                    target_type=TargetType.REST,
                    method=HttpMethod.POST,
                    url="https://example.com",
                    payload={},
                    extra_headers={},
                    retry={"interval_sequence": [1, 1], "retryable_errors": [500]},
                )

        assert mock_executor.execute.await_count == 3
        mock_run_repo.mark_error.assert_awaited_once()
        # mark_error receives only (run_id, error_text) — no attempt history persisted.
        assert len(mock_run_repo.mark_error.call_args.args) == 2
        assert any(log.get("event") == "last_retry_failed" for log in logs)
        assert any(log.get("event") == "run_failed" and log.get("attempts") == 3 for log in logs)

    async def test_legacy_kwargs_without_timeout_retry_single_attempt(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=ScheduleRunError("500", schedule_id=SCHEDULE_ID, run_id="r", status_code=500)
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs():
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(
                    schedule_id=SCHEDULE_ID,
                    target_type=TargetType.REST,
                    method=HttpMethod.POST,
                    url="https://example.com",
                    payload={},
                    extra_headers={},
                )

        assert mock_executor.execute.await_count == 1
        mock_run_repo.mark_error.assert_awaited_once()


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

    async def test_touches_heartbeat_marker(self, mocker):
        from src.worker.jobs import heartbeat

        mock_touch = mocker.patch("src.worker.jobs.touch_heartbeat")

        await heartbeat()

        mock_touch.assert_called_once()

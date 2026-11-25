import asyncio
import uuid

from pydantic import SecretStr
import pytest
from structlog.contextvars import clear_contextvars, get_contextvars
import structlog.testing

from src.lib.db.enums import HttpMethod, TargetType
from src.lib.exceptions.errors import (
    ConversationCreationError,
    OboCredentialsError,
    ScheduleRunError,
    StreamIdleTimeoutError,
)
from src.lib.schemas.conversation import ConversationLocation
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties


SCHEDULE_ID = "sched-jobs-001"
CONV_RUN_ID = "run-conv-loc-001"
CONV_LOCATION = ConversationLocation(
    deployment_id="gpt-4o", bucket="bkt", schedule_id=SCHEDULE_ID, run_id=CONV_RUN_ID, display_name="Daily"
)
CONV_ID = f"conversations/bkt/.scheduler/{SCHEDULE_ID}/gpt-4o__Daily__{CONV_RUN_ID}"


@pytest.fixture(autouse=True)
def reset_context():
    clear_contextvars()
    yield
    clear_contextvars()


@pytest.fixture(autouse=True)
def mock_credentials(mocker):
    """Provide working OBO credential resolution for all job tests.

    Patches the schedule lookup and DialClient so ``_resolve_credentials`` returns a
    valid header by default. Individual tests override ``get_obo_credentials`` /
    ``get_by_id`` to exercise credential-failure paths.
    """
    schedule = mocker.MagicMock(service_id="dial-native", created_by="user-sub-1", is_deleted=False)
    schedule_repo = mocker.MagicMock()
    schedule_repo.get_by_id = mocker.AsyncMock(return_value=schedule)
    mocker.patch("src.worker.jobs.ScheduleRepository", return_value=schedule_repo)

    creds = OboCredentials(header_name="Api-Key", header_value=SecretStr("obo-token"))
    dial_client = mocker.MagicMock()
    dial_client.get_obo_credentials = mocker.AsyncMock(return_value=creds)
    mocker.patch("src.worker.jobs.DialClient", return_value=dial_client)
    mocker.patch("src.worker.jobs.HttpClientProvider")
    mocker.patch(
        "src.worker.jobs.get_settings",
        return_value=mocker.MagicMock(
            scheduler_app_id="dial_scheduler",
            dial_url="https://dial.example.com",
            dial_api_key=mocker.MagicMock(get_secret_value=lambda: "svc-key"),
        ),
    )
    return mocker.MagicMock(schedule=schedule, schedule_repo=schedule_repo, dial_client=dial_client, creds=creds)


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


class TestDeletedScheduleGuard:
    """A deleted schedule must produce no execution and no run record (FR-006).

    The API and the worker are separate processes sharing one job store, so the worker can
    already have a due job in flight when the API unregisters it.
    """

    @staticmethod
    def _rest_kwargs() -> dict:
        return {
            "schedule_id": SCHEDULE_ID,
            "target_type": TargetType.REST,
            "method": HttpMethod.POST,
            "url": "https://example.com",
            "payload": {},
            "extra_headers": {},
        }

    async def test_soft_deleted_schedule_creates_no_run_record(self, mocker, mock_run_repo, mock_credentials):
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.schedule.is_deleted = True
        mock_executor = mocker.AsyncMock()
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            await execute_scheduled_job(**self._rest_kwargs())

        mock_run_repo.create_in_progress.assert_not_awaited()
        mock_executor.execute.assert_not_awaited()
        skipped = [log for log in logs if log.get("event") == "run_skipped_schedule_deleted"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "schedule_deleted"

    async def test_missing_schedule_creates_no_run_record(self, mocker, mock_run_repo, mock_credentials):
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.schedule_repo.get_by_id = mocker.AsyncMock(return_value=None)
        mock_executor = mocker.AsyncMock()
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            await execute_scheduled_job(**self._rest_kwargs())

        mock_run_repo.create_in_progress.assert_not_awaited()
        mock_executor.execute.assert_not_awaited()
        skipped = [log for log in logs if log.get("event") == "run_skipped_schedule_deleted"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "schedule_missing"

    async def test_live_schedule_still_executes(self, mocker, mock_run_repo, mock_credentials):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            await execute_scheduled_job(**self._rest_kwargs())

        mock_run_repo.create_in_progress.assert_awaited_once()
        mock_executor.execute.assert_awaited_once()
        assert not [log for log in logs if log.get("event") == "run_skipped_schedule_deleted"]

    async def test_guard_lookup_failure_does_not_drop_a_legitimate_run(self, mocker, mock_run_repo, mock_credentials):
        """An unexpected lookup failure must not silently swallow the run."""
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.schedule_repo.get_by_id = mocker.AsyncMock(
            side_effect=[RuntimeError("db blip"), mock_credentials.schedule]
        )
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            await execute_scheduled_job(**self._rest_kwargs())

        mock_run_repo.create_in_progress.assert_awaited_once()
        mock_executor.execute.assert_awaited_once()
        assert any(log.get("event") == "schedule_delete_check_failed" for log in logs)


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
                url="https://example.com/openai/",
                payload={"model": "gpt-4"},
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

    async def test_idle_expiry_is_persisted_with_its_own_reason(self, mocker, mock_run_repo):
        """FR-011: which bound expired must be visible in the persisted failure reason, not only in logs."""
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=StreamIdleTimeoutError(
                "stream produced no event for 60.0s", schedule_id=SCHEDULE_ID, run_id="r"
            )
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs():
            with pytest.raises(StreamIdleTimeoutError):
                await execute_scheduled_job(
                    schedule_id=SCHEDULE_ID,
                    target_type=TargetType.SSE,
                    method=HttpMethod.POST,
                    url="https://example.com",
                    payload={},
                    extra_headers={},
                )

        error_text = mock_run_repo.mark_error.call_args.args[1]
        assert "stream produced no event for 60.0s" in error_text
        assert "execution timed out" not in error_text

    async def test_ceiling_expiry_is_persisted_with_its_own_reason(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        async def never_finishes(*args, **kwargs):
            await asyncio.sleep(1)

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = never_finishes
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
                    timeout=0.01,
                )

        error_text = mock_run_repo.mark_error.call_args.args[1]
        assert "execution timed out after 0.01s" in error_text
        assert "stream produced no event" not in error_text


class TestExecuteScheduledJobCredentials:
    async def test_injects_obo_header_into_credentials(self, mocker, mock_run_repo):
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

        credentials = mock_executor.execute.call_args.args[1]
        assert credentials.header_name == "Api-Key"
        assert credentials.header_value.get_secret_value() == "obo-token"

    async def test_requests_credentials_for_owner_and_service(self, mocker, mock_run_repo, mock_credentials):
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

        call = mock_credentials.dial_client.get_obo_credentials.call_args
        assert call.args[0] == "svc-key"
        request = call.args[1]
        assert request.owner_user_id == "user-sub-1"
        assert request.url == "applications/dial_scheduler/external_services/dial-native"

    async def test_logs_credentials_fetch_lifecycle(self, mocker, mock_run_repo):
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

        events = {log.get("event") for log in logs}
        assert "credentials_fetch_start" in events
        assert "credentials_fetch_succeeded" in events
        # The OBO secret must never be logged.
        for log in logs:
            assert "obo-token" not in str(log)


class TestExecuteScheduledJobCredentialFailure:
    async def test_obo_failure_marks_credentials_error_and_skips_target(self, mocker, mock_run_repo, mock_credentials):
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.dial_client.get_obo_credentials = mocker.AsyncMock(
            side_effect=OboCredentialsError("no credentials", status_code=404, response_body={"error": "not signed in"})
        )
        mock_executor = mocker.AsyncMock()
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

        # Target request never sent, and the target-stage persistence path is not used.
        mock_executor.execute.assert_not_awaited()
        mock_run_repo.mark_error.assert_not_awaited()

        mock_run_repo.mark_credentials_error.assert_awaited_once()
        run_error = mock_run_repo.mark_credentials_error.call_args.args[1]
        assert run_error.stage == "credentials"
        assert run_error.credentials_response == {"status": 404, "body": {"error": "not signed in"}}
        assert any(log.get("event") == "credentials_fetch_failed" for log in logs)

    async def test_schedule_vanishing_mid_run_marks_credentials_error(self, mocker, mock_run_repo, mock_credentials):
        """The pre-flight guard sees a live schedule; it is gone by the credentials stage."""
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.schedule_repo.get_by_id = mocker.AsyncMock(side_effect=[mock_credentials.schedule, None])
        mock_executor = mocker.AsyncMock()
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

        mock_executor.execute.assert_not_awaited()
        mock_run_repo.mark_credentials_error.assert_awaited_once()
        assert mock_run_repo.mark_credentials_error.call_args.args[1].stage == "credentials"

    async def test_schedule_lookup_exception_marks_credentials_error(self, mocker, mock_run_repo, mock_credentials):
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.schedule_repo.get_by_id = mocker.AsyncMock(side_effect=RuntimeError("db down"))
        mock_executor = mocker.AsyncMock()
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

        mock_executor.execute.assert_not_awaited()
        mock_run_repo.mark_credentials_error.assert_awaited_once()
        assert mock_run_repo.mark_credentials_error.call_args.args[1].stage == "credentials"
        assert any(log.get("event") == "credentials_fetch_failed" for log in logs)

    async def test_credentials_error_persist_failure_is_logged(self, mocker, mock_run_repo, mock_credentials):
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.dial_client.get_obo_credentials = mocker.AsyncMock(
            side_effect=OboCredentialsError("no credentials", status_code=404, response_body={})
        )
        mock_run_repo.mark_credentials_error = mocker.AsyncMock(side_effect=Exception("DB gone"))
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mocker.AsyncMock())

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

        assert any(log.get("event") == "run_persist_failed" for log in logs)

    async def test_credential_failure_does_not_consume_retry_policy(self, mocker, mock_run_repo, mock_credentials):
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.dial_client.get_obo_credentials = mocker.AsyncMock(
            side_effect=OboCredentialsError("no credentials", status_code=404, response_body={})
        )
        exec_with_policy = mocker.patch("src.worker.jobs.execute_with_policy")

        with structlog.testing.capture_logs():
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

        exec_with_policy.assert_not_called()


class TestFailureLogLevel:
    """Error level is reserved for the scheduler's own faults (DB failures, bugs).

    Failures caused by anything outside this service — DIAL rejecting or not answering an OBO
    request, a deleted schedule, invalid stored properties, a failing target — log at warning.
    """

    @staticmethod
    def _rest_kwargs(**overrides) -> dict:
        kwargs = dict(
            schedule_id=SCHEDULE_ID,
            target_type=TargetType.REST,
            method=HttpMethod.POST,
            url="https://example.com",
            payload={},
            extra_headers={},
        )
        kwargs.update(overrides)
        return kwargs

    @staticmethod
    def _events(logs: list[dict], event: str) -> list[dict]:
        return [log for log in logs if log.get("event") == event]

    @pytest.mark.parametrize("status_code", [404, 500, None])
    async def test_obo_failure_logged_as_warning(self, mocker, mock_run_repo, mock_credentials, status_code):
        """DIAL rejecting the OBO request (4xx), failing (5xx), or being unreachable (no status)
        are all outside this service."""
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.dial_client.get_obo_credentials = mocker.AsyncMock(
            side_effect=OboCredentialsError("no credentials", status_code=status_code, response_body={})
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mocker.AsyncMock())

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(**self._rest_kwargs())

        failures = self._events(logs, "credentials_fetch_failed")
        assert len(failures) == 1
        assert failures[0]["log_level"] == "warning"
        assert failures[0]["status_code"] == status_code

    async def test_schedule_vanishing_mid_run_logged_as_warning(self, mocker, mock_run_repo, mock_credentials):
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.schedule_repo.get_by_id = mocker.AsyncMock(side_effect=[mock_credentials.schedule, None])
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mocker.AsyncMock())

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(**self._rest_kwargs())

        failures = self._events(logs, "credentials_fetch_failed")
        assert len(failures) == 1
        assert failures[0]["log_level"] == "warning"
        assert failures[0]["reason"] == "schedule_not_found"

    async def test_schedule_lookup_failure_stays_error(self, mocker, mock_run_repo, mock_credentials):
        """A failed DB read is our own infrastructure failing, so it stays at error level."""
        from src.worker.jobs import execute_scheduled_job

        mock_credentials.schedule_repo.get_by_id = mocker.AsyncMock(side_effect=RuntimeError("db down"))
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mocker.AsyncMock())

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(**self._rest_kwargs())

        failures = self._events(logs, "credentials_fetch_failed")
        assert len(failures) == 1
        assert failures[0]["log_level"] == "error"
        assert failures[0]["reason"] == "schedule_lookup_failed"

    async def test_invalid_properties_logged_as_warning(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mocker.patch("src.worker.jobs.resolve_executor")

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                # missing required method for REST
                await execute_scheduled_job(**self._rest_kwargs(method=None))

        failures = self._events(logs, "run_failed")
        assert len(failures) == 1
        assert failures[0]["log_level"] == "warning"
        assert failures[0]["reason"] == "invalid_schedule_properties"

    @pytest.mark.parametrize("status_code", [403, 502, None])
    async def test_terminal_target_failure_logged_as_warning_with_status(self, mocker, mock_run_repo, status_code):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=ScheduleRunError("boom", schedule_id=SCHEDULE_ID, run_id="r", status_code=status_code)
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(**self._rest_kwargs())

        failures = self._events(logs, "run_failed")
        assert len(failures) == 1
        assert failures[0]["log_level"] == "warning"
        # The level no longer distinguishes a client error from a target outage, so the status
        # code must be on the event for alerting to key on.
        assert failures[0]["status_code"] == status_code

    async def test_unexpected_executor_error_stays_error_with_traceback(self, mocker, mock_run_repo):
        """A bug escaping an executor is the scheduler's own fault: error level, with a traceback,
        and the run is still persisted as an error."""
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(side_effect=AttributeError("bug: no attribute 'foo'"))
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(**self._rest_kwargs())

        failures = self._events(logs, "run_failed")
        assert len(failures) == 1
        assert failures[0]["log_level"] == "error"
        assert failures[0]["reason"] == "unexpected_error"
        assert failures[0]["exc_info"] is True
        mock_run_repo.mark_error.assert_awaited_once()
        assert "no attribute 'foo'" in mock_run_repo.mark_error.call_args.args[1]

    async def test_persist_failure_stays_error(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_run_repo.mark_success = mocker.AsyncMock(side_effect=Exception("DB gone"))
        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200, "body": {}})
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            await execute_scheduled_job(**self._rest_kwargs())

        failures = self._events(logs, "run_persist_failed")
        assert len(failures) == 1
        assert failures[0]["log_level"] == "error"

    async def test_target_failure_emits_no_error_level_logs(self, mocker, mock_run_repo):
        """End-to-end guard: a purely remote failure must not put anything on the error channel."""
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(
            side_effect=ScheduleRunError("401", schedule_id=SCHEDULE_ID, run_id="r", status_code=401)
        )
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_scheduled_job(**self._rest_kwargs())

        assert [log for log in logs if log.get("log_level") == "error"] == []


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


class TestMaybeCreateConversationIsBestEffort:
    """FR-011: a conversation failure is logged and swallowed, never altering the run outcome."""

    @staticmethod
    def _properties():
        return ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://dial.example.com/openai/",
            payload={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            api_version="2024-02-01",
            create_conversation=True,
        )

    @staticmethod
    def _ctx():
        return RunContext(run_id="run-fr011", schedule_id=SCHEDULE_ID, conversation_location=CONV_LOCATION)

    @staticmethod
    def _creds():
        return OboCredentials(header_name="Api-Key", header_value=SecretStr("owner-token"))

    async def test_failure_is_swallowed_and_logged_once(self, mocker):
        from src.worker.jobs import _maybe_create_conversation

        mocker.patch(
            "src.worker.jobs.create_run_conversation",
            side_effect=ConversationCreationError("dial unreachable"),
        )

        with structlog.testing.capture_logs() as logs:
            # Must not raise: the run has already succeeded by this point.
            await _maybe_create_conversation(self._ctx(), self._creds(), self._properties(), {"result": {}})

        failures = [log for log in logs if log.get("event") == "conversation_create_failed"]
        assert len(failures) == 1
        assert failures[0]["run_id"] == "run-fr011"
        assert failures[0]["schedule_id"] == SCHEDULE_ID
        # Best-effort and non-fatal: never on the error channel.
        assert failures[0]["log_level"] == "warning"

    async def test_failure_log_carries_no_secret_or_prompt_content(self, mocker):
        from src.worker.jobs import _maybe_create_conversation

        mocker.patch("src.worker.jobs.create_run_conversation", side_effect=RuntimeError("boom"))
        creds = OboCredentials(header_name="Api-Key", header_value=SecretStr("super-secret"))

        with structlog.testing.capture_logs() as logs:
            await _maybe_create_conversation(self._ctx(), creds, self._properties(), {"result": {}})

        for log in logs:
            assert "super-secret" not in str(log)

    async def test_skipped_when_not_opted_in(self, mocker):
        from src.worker.jobs import _maybe_create_conversation

        create = mocker.patch("src.worker.jobs.create_run_conversation")
        properties = self._properties().model_copy(update={"create_conversation": False})

        await _maybe_create_conversation(self._ctx(), self._creds(), properties, {"result": {}})

        create.assert_not_called()


class TestMaybeCreateConversationPersistId:
    """Tests for conversation_id persistence in _maybe_create_conversation (data-model §6 cases 11–15)."""

    CONV_ID = "conversations/bkt/.scheduler/sched-001/gpt-4o__hello__run-001"

    @staticmethod
    def _properties(create_conversation: bool = True, target_type: TargetType = TargetType.CHAT_COMPLETION):
        return ScheduleProperties(
            target_type=target_type,
            url="https://dial.example.com/openai/",
            payload={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            api_version="2024-02-01",
            create_conversation=create_conversation,
        )

    @staticmethod
    def _ctx():
        return RunContext(run_id="run-persist-001", schedule_id=SCHEDULE_ID, conversation_location=CONV_LOCATION)

    @staticmethod
    def _creds():
        return OboCredentials(header_name="Api-Key", header_value=SecretStr("owner-token"))

    _SENTINEL = object()

    def _setup_repo(self, mocker, return_value=_SENTINEL):
        """Patch ScheduleRunRepository and Database so set_conversation_id is controllable.

        Pass ``return_value=None`` to simulate the repository returning ``None`` (run row missing).
        Omit the argument (or pass ``_SENTINEL``) to get a non-None MagicMock (success case).
        """
        mock_repo = mocker.AsyncMock()
        mock_repo.set_conversation_id = mocker.AsyncMock(
            return_value=mocker.MagicMock() if return_value is self._SENTINEL else return_value
        )
        mocker.patch("src.worker.jobs.ScheduleRunRepository", return_value=mock_repo)
        session = mocker.MagicMock()
        session.__aenter__ = mocker.AsyncMock(return_value=session)
        session.__aexit__ = mocker.AsyncMock(return_value=None)
        database = mocker.MagicMock()
        database.sessionmaker.return_value = session
        mocker.patch("src.worker.jobs.Database", return_value=database)
        return mock_repo

    async def test_successful_creation_calls_set_conversation_id_once(self, mocker):
        """Case 11: successful conversation creation → set_conversation_id called once."""
        from src.worker.jobs import _maybe_create_conversation

        mocker.patch("src.worker.jobs.create_run_conversation", return_value=self.CONV_ID)
        mock_repo = self._setup_repo(mocker)

        with structlog.testing.capture_logs():
            await _maybe_create_conversation(self._ctx(), self._creds(), self._properties(), {"result": {}})

        mock_repo.set_conversation_id.assert_awaited_once_with("run-persist-001", self.CONV_ID)

    async def test_successful_persist_logs_conversation_id_persisted(self, mocker):
        """Case 11: successful persist logs conversation_id_persisted at info level."""
        from src.worker.jobs import _maybe_create_conversation

        mocker.patch("src.worker.jobs.create_run_conversation", return_value=self.CONV_ID)
        self._setup_repo(mocker)

        with structlog.testing.capture_logs() as logs:
            await _maybe_create_conversation(self._ctx(), self._creds(), self._properties(), {"result": {}})

        persisted = [log for log in logs if log.get("event") == "conversation_id_persisted"]
        assert len(persisted) == 1
        assert persisted[0]["run_id"] == "run-persist-001"
        assert persisted[0]["schedule_id"] == SCHEDULE_ID
        assert persisted[0]["log_level"] == "info"

    async def test_conversation_creation_error_does_not_call_set_conversation_id(self, mocker):
        """Case 12: ConversationCreationError → set_conversation_id NOT called."""
        from src.worker.jobs import _maybe_create_conversation

        mocker.patch(
            "src.worker.jobs.create_run_conversation",
            side_effect=ConversationCreationError("dial unreachable"),
        )
        mock_repo = self._setup_repo(mocker)

        with structlog.testing.capture_logs() as logs:
            await _maybe_create_conversation(self._ctx(), self._creds(), self._properties(), {"result": {}})

        mock_repo.set_conversation_id.assert_not_awaited()
        failed = [log for log in logs if log.get("event") == "conversation_create_failed"]
        assert len(failed) == 1

    async def test_db_error_in_set_conversation_id_is_swallowed(self, mocker):
        """Case 13: set_conversation_id raising DB error → swallowed, logged as warning."""
        from src.worker.jobs import _maybe_create_conversation

        mocker.patch("src.worker.jobs.create_run_conversation", return_value=self.CONV_ID)
        mock_repo = self._setup_repo(mocker)
        mock_repo.set_conversation_id.side_effect = RuntimeError("DB gone")

        with structlog.testing.capture_logs() as logs:
            # Must not raise — best-effort
            await _maybe_create_conversation(self._ctx(), self._creds(), self._properties(), {"result": {}})

        # The DB error is caught by the existing except; logged as conversation_create_failed
        failed = [log for log in logs if log.get("event") == "conversation_create_failed"]
        assert len(failed) == 1
        assert failed[0]["log_level"] == "warning"

    async def test_set_conversation_id_returning_none_logs_persist_failed(self, mocker):
        """Case 14: set_conversation_id returning None → conversation_id_persist_failed logged."""
        from src.worker.jobs import _maybe_create_conversation

        mocker.patch("src.worker.jobs.create_run_conversation", return_value=self.CONV_ID)
        self._setup_repo(mocker, return_value=None)

        with structlog.testing.capture_logs() as logs:
            await _maybe_create_conversation(self._ctx(), self._creds(), self._properties(), {"result": {}})

        failed = [log for log in logs if log.get("event") == "conversation_id_persist_failed"]
        assert len(failed) == 1
        assert failed[0]["run_id"] == "run-persist-001"
        assert failed[0]["schedule_id"] == SCHEDULE_ID
        assert failed[0]["log_level"] == "warning"

    async def test_create_conversation_false_skips_set_conversation_id(self, mocker):
        """Case 15: create_conversation=False → set_conversation_id not called."""
        from src.worker.jobs import _maybe_create_conversation

        create = mocker.patch("src.worker.jobs.create_run_conversation")
        mock_repo = self._setup_repo(mocker)
        properties = self._properties(create_conversation=False)

        with structlog.testing.capture_logs():
            await _maybe_create_conversation(self._ctx(), self._creds(), properties, {"result": {}})

        create.assert_not_called()
        mock_repo.set_conversation_id.assert_not_awaited()

    async def test_non_chat_completion_skips_set_conversation_id(self, mocker):
        """Case 15: non-chat-completion target_type → set_conversation_id not called."""
        from src.lib.db.enums import HttpMethod
        from src.worker.jobs import _maybe_create_conversation

        create = mocker.patch("src.worker.jobs.create_run_conversation")
        mock_repo = self._setup_repo(mocker)
        properties = ScheduleProperties(
            target_type=TargetType.REST,
            method=HttpMethod.POST,
            url="https://example.com",
            payload={},
            extra_headers={},
        )

        with structlog.testing.capture_logs():
            await _maybe_create_conversation(self._ctx(), self._creds(), properties, {"result": {}})

        create.assert_not_called()
        mock_repo.set_conversation_id.assert_not_awaited()


def _chat_snapshot(**overrides) -> dict:
    snapshot = {
        "target_type": TargetType.CHAT_COMPLETION,
        "url": "https://dial.example.com/openai/",
        "payload": {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        "extra_headers": {},
        "api_version": "2024-02-01",
        "create_conversation": True,
    }
    snapshot.update(overrides)
    return snapshot


_OTHER_TARGET_SNAPSHOTS = {
    "chat_completion_not_opted_in": _chat_snapshot(create_conversation=False),
    "responses": {
        "target_type": TargetType.RESPONSES,
        "url": "https://dial.example.com/openai/",
        "payload": {"model": "gpt-4o", "input": "hi"},
        "api_version": "2024-02-01",
    },
    "rest": {"target_type": TargetType.REST, "method": HttpMethod.POST, "url": "https://example.com", "payload": {}},
    "sse": {"target_type": TargetType.SSE, "method": HttpMethod.POST, "url": "https://example.com", "payload": {}},
}


def _conv_ctx() -> RunContext:
    return RunContext(run_id=CONV_RUN_ID, schedule_id=SCHEDULE_ID)


@pytest.fixture
def conversation_mocks(mocker, mock_run_repo):
    """Patch location resolution, conversation creation and the executor for execute_run tests."""
    calls: list[str] = []
    seen_locations: list = []

    async def _resolve(ctx, credentials, properties):
        calls.append("resolve")
        return CONV_LOCATION

    async def _execute(ctx, credentials, properties):
        calls.append("execute")
        seen_locations.append(ctx.conversation_location)
        return {"result": {"id": "cmpl-1", "choices": []}}

    resolve = mocker.patch("src.worker.jobs.resolve_conversation_location", side_effect=_resolve)
    create = mocker.patch("src.worker.jobs.create_run_conversation", return_value=CONV_ID)
    executor = mocker.AsyncMock()
    executor.execute = mocker.AsyncMock(side_effect=_execute)
    mocker.patch("src.worker.jobs.resolve_executor", return_value=executor)
    return mocker.MagicMock(
        resolve=resolve,
        create=create,
        executor=executor,
        calls=calls,
        seen_locations=seen_locations,
        run_repo=mock_run_repo,
    )


class TestConversationLocationInExecuteRun:
    """US1: the location is resolved once, before any attempt, and reused for the conversation."""

    async def test_resolved_before_the_target_request(self, conversation_mocks):
        from src.worker.jobs import execute_run

        with structlog.testing.capture_logs():
            await execute_run(_conv_ctx(), _chat_snapshot())

        conversation_mocks.resolve.assert_awaited_once()
        assert conversation_mocks.calls == ["resolve", "execute"]
        assert conversation_mocks.seen_locations == [CONV_LOCATION]

    async def test_resolved_with_the_owner_credentials(self, conversation_mocks, mock_credentials):
        from src.worker.jobs import execute_run

        with structlog.testing.capture_logs():
            await execute_run(_conv_ctx(), _chat_snapshot())

        assert conversation_mocks.resolve.call_args.args[1] is mock_credentials.creds

    async def test_every_retry_attempt_sees_the_same_location(self, mocker, conversation_mocks):
        from src.worker.jobs import execute_run

        mocker.patch("src.worker.retry.asyncio.sleep", new=mocker.AsyncMock())
        outcomes = iter(
            [
                ScheduleRunError("503", schedule_id=SCHEDULE_ID, run_id=CONV_RUN_ID, status_code=503),
                ScheduleRunError("503", schedule_id=SCHEDULE_ID, run_id=CONV_RUN_ID, status_code=503),
                {"result": {"id": "cmpl-1", "choices": []}},
            ]
        )

        async def _execute(ctx, credentials, properties):
            conversation_mocks.seen_locations.append(ctx.conversation_location)
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        conversation_mocks.executor.execute.side_effect = _execute
        snapshot = _chat_snapshot(retry={"interval_sequence": [1, 1], "retryable_errors": [503]})

        with structlog.testing.capture_logs():
            await execute_run(_conv_ctx(), snapshot)

        conversation_mocks.resolve.assert_awaited_once()
        assert len(conversation_mocks.seen_locations) == 3
        assert all(location is conversation_mocks.seen_locations[0] for location in conversation_mocks.seen_locations)
        assert conversation_mocks.seen_locations[0] == CONV_LOCATION

    @pytest.mark.parametrize("snapshot", _OTHER_TARGET_SNAPSHOTS.values(), ids=_OTHER_TARGET_SNAPSHOTS.keys())
    async def test_not_resolved_for_other_targets_or_without_opt_in(self, conversation_mocks, snapshot):
        from src.worker.jobs import execute_run

        conversation_mocks.executor.execute.side_effect = None
        conversation_mocks.executor.execute.return_value = {"status_code": 200, "body": {}}

        with structlog.testing.capture_logs():
            await execute_run(_conv_ctx(), snapshot)

        conversation_mocks.resolve.assert_not_awaited()
        ctx = conversation_mocks.executor.execute.call_args.args[0]
        assert ctx.conversation_location is None

    async def test_success_saves_at_the_resolved_location(self, conversation_mocks):
        from src.worker.jobs import execute_run

        with structlog.testing.capture_logs():
            await execute_run(_conv_ctx(), _chat_snapshot())

        conversation_mocks.create.assert_awaited_once()
        ctx = conversation_mocks.create.call_args.args[0]
        assert ctx.conversation_location == CONV_LOCATION
        conversation_mocks.run_repo.set_conversation_id.assert_awaited_once_with(CONV_RUN_ID, CONV_ID)

    async def test_target_failure_still_sent_the_location_but_saves_nothing(self, conversation_mocks):
        from src.worker.jobs import execute_run

        async def _fail(ctx, credentials, properties):
            conversation_mocks.seen_locations.append(ctx.conversation_location)
            raise ScheduleRunError("500", schedule_id=SCHEDULE_ID, run_id=CONV_RUN_ID, status_code=500)

        conversation_mocks.executor.execute.side_effect = _fail

        with structlog.testing.capture_logs():
            with pytest.raises(ScheduleRunError):
                await execute_run(_conv_ctx(), _chat_snapshot())

        assert conversation_mocks.seen_locations == [CONV_LOCATION]
        conversation_mocks.create.assert_not_awaited()
        conversation_mocks.run_repo.mark_error.assert_awaited_once()

    async def test_resolution_is_logged(self, conversation_mocks):
        from src.worker.jobs import execute_run

        with structlog.testing.capture_logs() as logs:
            await execute_run(_conv_ctx(), _chat_snapshot())

        resolved = [log for log in logs if log.get("event") == "conversation_id_resolved"]
        assert len(resolved) == 1
        assert resolved[0]["log_level"] == "info"
        assert resolved[0]["run_id"] == CONV_RUN_ID
        assert resolved[0]["schedule_id"] == SCHEDULE_ID
        assert resolved[0]["deployment_kind"] == "model"

    async def test_application_deployment_kind_is_logged(self, conversation_mocks):
        from src.worker.jobs import execute_run

        conversation_mocks.resolve.side_effect = None
        conversation_mocks.resolve.return_value = CONV_LOCATION.model_copy(
            update={"deployment_id": "applications/bkt/My App"}
        )

        with structlog.testing.capture_logs() as logs:
            await execute_run(_conv_ctx(), _chat_snapshot())

        resolved = [log for log in logs if log.get("event") == "conversation_id_resolved"]
        assert resolved[0]["deployment_kind"] == "application"


_RESOLVE_FAILURES = {
    "bucket_status": ConversationCreationError("bucket request failed with status 503"),
    "bucket_missing": ConversationCreationError("bucket response missing 'bucket'"),
    "invalid_model": ConversationCreationError("payload.model must be a non-empty string to create a conversation"),
    "schedule_missing": ConversationCreationError(f"schedule not found: {SCHEDULE_ID}"),
    "unexpected": RuntimeError("boom"),
}


class TestConversationLocationFailure:
    """US2: a failed resolution never breaks the run; the header is omitted and nothing is saved."""

    @pytest.mark.parametrize("failure", _RESOLVE_FAILURES.values(), ids=_RESOLVE_FAILURES.keys())
    async def test_run_succeeds_without_location_or_conversation(self, conversation_mocks, failure):
        from src.worker.jobs import execute_run

        conversation_mocks.resolve.side_effect = failure

        with structlog.testing.capture_logs() as logs:
            await execute_run(_conv_ctx(), _chat_snapshot())

        conversation_mocks.executor.execute.assert_awaited_once()
        assert conversation_mocks.seen_locations == [None]
        conversation_mocks.run_repo.mark_success.assert_awaited_once()
        conversation_mocks.create.assert_not_awaited()
        conversation_mocks.run_repo.set_conversation_id.assert_not_awaited()

        failed = [log for log in logs if log.get("event") == "conversation_id_resolve_failed"]
        assert len(failed) == 1
        assert failed[0]["log_level"] == "warning"
        assert failed[0]["run_id"] == CONV_RUN_ID
        assert failed[0]["schedule_id"] == SCHEDULE_ID
        assert failed[0]["reason"] == str(failure)

        skipped = [log for log in logs if log.get("event") == "conversation_create_skipped"]
        assert len(skipped) == 1
        assert skipped[0]["log_level"] == "info"
        assert skipped[0]["reason"] == "conversation_id_unresolved"
        assert skipped[0]["run_id"] == CONV_RUN_ID

        for log in logs:
            assert "obo-token" not in str(log)

    async def test_target_failure_is_recorded_as_before(self, conversation_mocks):
        from src.worker.jobs import execute_run

        conversation_mocks.resolve.side_effect = ConversationCreationError("bucket request failed with status 503")
        conversation_mocks.executor.execute.side_effect = ScheduleRunError(
            "500", schedule_id=SCHEDULE_ID, run_id=CONV_RUN_ID, status_code=500
        )

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleRunError):
                await execute_run(_conv_ctx(), _chat_snapshot())

        conversation_mocks.run_repo.mark_error.assert_awaited_once()
        conversation_mocks.run_repo.mark_success.assert_not_awaited()
        assert len([log for log in logs if log.get("event") == "conversation_id_resolve_failed"]) == 1
        assert not [log for log in logs if log.get("event") == "conversation_create_skipped"]
        conversation_mocks.create.assert_not_awaited()

    async def test_resolution_is_attempted_once_across_retries(self, mocker, conversation_mocks):
        from src.worker.jobs import execute_run

        mocker.patch("src.worker.retry.asyncio.sleep", new=mocker.AsyncMock())
        conversation_mocks.resolve.side_effect = ConversationCreationError("bucket request failed with status 503")
        conversation_mocks.executor.execute.side_effect = [
            ScheduleRunError("503", schedule_id=SCHEDULE_ID, run_id=CONV_RUN_ID, status_code=503),
            {"result": {"id": "cmpl-1", "choices": []}},
        ]
        snapshot = _chat_snapshot(retry={"interval_sequence": [1], "retryable_errors": [503]})

        with structlog.testing.capture_logs() as logs:
            await execute_run(_conv_ctx(), snapshot)

        conversation_mocks.resolve.assert_awaited_once()
        assert len([log for log in logs if log.get("event") == "conversation_id_resolve_failed"]) == 1


class TestConversationLogContext:
    """US5: once resolved, the raw conversation id is on every later log entry of the run."""

    async def test_resolved_id_is_bound_for_later_events(self, conversation_mocks):
        from src.worker.jobs import execute_run

        bound_during_attempt: list[dict] = []

        async def _execute(ctx, credentials, properties):
            bound_during_attempt.append(dict(get_contextvars()))
            return {"result": {"id": "cmpl-1", "choices": []}}

        conversation_mocks.executor.execute.side_effect = _execute

        with structlog.testing.capture_logs():
            await execute_run(_conv_ctx(), _chat_snapshot())

        assert bound_during_attempt[0]["conversation_id"] == CONV_ID
        assert get_contextvars()["conversation_id"] == CONV_ID

    async def test_nothing_is_bound_when_resolution_fails(self, conversation_mocks):
        from src.worker.jobs import execute_run

        conversation_mocks.resolve.side_effect = ConversationCreationError("bucket request failed with status 503")

        with structlog.testing.capture_logs():
            await execute_run(_conv_ctx(), _chat_snapshot())

        assert "conversation_id" not in get_contextvars()

    async def test_scheduled_job_clears_the_bound_id_afterwards(self, mocker, conversation_mocks):
        from src.worker.jobs import execute_scheduled_job

        snapshot = _chat_snapshot()
        with structlog.testing.capture_logs():
            await execute_scheduled_job(schedule_id=SCHEDULE_ID, **snapshot)

        conversation_mocks.resolve.assert_awaited_once()
        assert get_contextvars() == {}

    async def test_unexpected_resolver_error_keeps_the_traceback(self, conversation_mocks):
        from src.worker.jobs import execute_run

        conversation_mocks.resolve.side_effect = RuntimeError("boom")

        with structlog.testing.capture_logs() as logs:
            await execute_run(_conv_ctx(), _chat_snapshot())

        failed = [log for log in logs if log.get("event") == "conversation_id_resolve_failed"]
        assert failed[0].get("exc_info") is True

    async def test_expected_resolver_error_has_no_traceback(self, conversation_mocks):
        from src.worker.jobs import execute_run

        conversation_mocks.resolve.side_effect = ConversationCreationError("bucket request failed with status 503")

        with structlog.testing.capture_logs() as logs:
            await execute_run(_conv_ctx(), _chat_snapshot())

        failed = [log for log in logs if log.get("event") == "conversation_id_resolve_failed"]
        assert "exc_info" not in failed[0]

    async def test_resolve_failed_warning_carries_trace_and_span_ids(self, conversation_mocks):
        from opentelemetry.sdk.trace import TracerProvider

        from src.lib.utils.logging import _add_trace_span_ids
        from src.worker.jobs import execute_run

        conversation_mocks.resolve.side_effect = ConversationCreationError("bucket request failed with status 503")
        tracer = TracerProvider().get_tracer("t")

        with tracer.start_as_current_span(f"job:schedule:{SCHEDULE_ID}") as span:
            with structlog.testing.capture_logs(processors=[_add_trace_span_ids]) as logs:
                await execute_run(_conv_ctx(), _chat_snapshot())
            span_ctx = span.get_span_context()

        failed = [log for log in logs if log.get("event") == "conversation_id_resolve_failed"]
        assert failed[0]["trace_id"] == format(span_ctx.trace_id, "032x")
        assert failed[0]["span_id"] == format(span_ctx.span_id, "016x")

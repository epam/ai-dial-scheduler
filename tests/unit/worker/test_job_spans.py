"""Tests verifying execute_scheduled_job runs within an OTel span."""

import pytest
import structlog.testing

from src.lib.db.enums import HttpMethod, TargetType


SCHEDULE_ID = "sched-span-001"


@pytest.fixture()
def mock_run_repo(mocker):
    import uuid

    created_row = mocker.MagicMock()
    created_row.id = uuid.uuid4()
    mock_repo = mocker.AsyncMock()
    mock_repo.create_in_progress = mocker.AsyncMock(return_value=created_row)
    mock_repo.mark_success = mocker.AsyncMock(return_value=mocker.MagicMock())
    mock_repo.mark_error = mocker.AsyncMock(return_value=mocker.MagicMock())
    mocker.patch("src.worker.jobs.ScheduleRunRepository", return_value=mock_repo)
    mocker.patch("src.worker.jobs.Database")
    return mock_repo


class TestHeartbeatSpan:
    async def test_heartbeat_starts_span(self, mocker):
        from src.worker.jobs import heartbeat

        mock_span_cm = mocker.MagicMock()
        mock_span_cm.__enter__ = mocker.MagicMock(return_value=mocker.MagicMock())
        mock_span_cm.__exit__ = mocker.MagicMock(return_value=False)

        mock_tracer = mocker.MagicMock()
        mock_tracer.start_as_current_span = mocker.MagicMock(return_value=mock_span_cm)
        mocker.patch("src.worker.jobs._tracer", mock_tracer)

        with structlog.testing.capture_logs():
            await heartbeat()

        mock_tracer.start_as_current_span.assert_called_once_with("job:heartbeat")


class TestJobSpanWrapping:
    async def test_execute_scheduled_job_starts_span(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        mock_executor = mocker.AsyncMock()
        mock_executor.execute = mocker.AsyncMock(return_value={"status_code": 200})
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        mock_span_cm = mocker.MagicMock()
        mock_span_cm.__enter__ = mocker.MagicMock(return_value=mocker.MagicMock())
        mock_span_cm.__exit__ = mocker.MagicMock(return_value=False)

        mock_tracer = mocker.MagicMock()
        mock_tracer.start_as_current_span = mocker.MagicMock(return_value=mock_span_cm)
        mocker.patch("src.worker.jobs._tracer", mock_tracer)

        with structlog.testing.capture_logs():
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                method=HttpMethod.POST,
                url="https://example.com",
                payload={},
                extra_headers={},
            )

        mock_tracer.start_as_current_span.assert_called_once_with(f"job:schedule:{SCHEDULE_ID}")

    async def test_execute_scheduled_job_span_wraps_execution(self, mocker, mock_run_repo):
        from src.worker.jobs import execute_scheduled_job

        call_order: list[str] = []

        mock_executor = mocker.AsyncMock()

        async def fake_execute(*_args, **_kwargs):
            call_order.append("execute")
            return {"status_code": 200}

        mock_executor.execute.side_effect = fake_execute
        mocker.patch("src.worker.jobs.resolve_executor", return_value=mock_executor)

        entered_calls: list[str] = []
        exited_calls: list[str] = []

        class TracedContext:
            def __enter__(self):
                entered_calls.append("enter")
                return self

            def __exit__(self, *_args):
                exited_calls.append("exit")
                return False

        mock_tracer = mocker.MagicMock()
        mock_tracer.start_as_current_span = mocker.MagicMock(return_value=TracedContext())
        mocker.patch("src.worker.jobs._tracer", mock_tracer)

        with structlog.testing.capture_logs():
            await execute_scheduled_job(
                schedule_id=SCHEDULE_ID,
                target_type=TargetType.REST,
                method=HttpMethod.POST,
                url="https://example.com",
                payload={},
                extra_headers={},
            )

        assert entered_calls == ["enter"]
        assert exited_calls == ["exit"]
        assert "execute" in call_order

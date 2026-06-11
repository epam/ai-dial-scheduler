from datetime import UTC, datetime
from unittest.mock import AsyncMock
import uuid

import pytest

from src.lib.db.enums import ScheduleRunStatus
from src.lib.exceptions.errors import ScheduleNotFoundError, ScheduleRunNotFoundError
from src.lib.schemas.pagination import Page, Paginator


SCHEDULE_ID = "sched-svc-001"
RUN_ID = str(uuid.uuid4())


def make_schedule(mocker):
    schedule = mocker.MagicMock()
    schedule.display_name = "Test Schedule"
    schedule.trigger = {"cron": {"fields": {"hour": 9}}}
    schedule.kwargs = {"target_type": "chat_completion", "url": "https://example.com"}
    return schedule


def make_run(mocker, run_id: str = RUN_ID, schedule_id: str = SCHEDULE_ID):
    run = mocker.MagicMock()
    run.id = uuid.UUID(run_id)
    run.status = ScheduleRunStatus.SUCCESS
    run.created_at = datetime.now(UTC)
    run.end_time = datetime.now(UTC)
    run.result = {"status_code": 200}
    run.schedule_id = schedule_id
    return run


class TestScheduleRunServiceListRuns:
    async def test_returns_paginated_list(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule = make_schedule(mocker)
        run = make_run(mocker)
        schedule_repo.get.return_value = (schedule, None)
        run_repo.list_for_schedule.return_value = (1, [run])

        service = ScheduleRunService(schedule_repo, run_repo)
        paginator = Paginator(limit=10, offset=0, base_path=f"/schedules/{SCHEDULE_ID}/runs")
        result = await service.list_runs(SCHEDULE_ID, "mock_user_id", paginator)

        assert isinstance(result, Page)
        assert result.count == 1
        assert len(result.results) == 1

    async def test_raises_schedule_not_found_when_missing(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule_repo.get.return_value = None

        service = ScheduleRunService(schedule_repo, run_repo)
        paginator = Paginator(limit=10, offset=0, base_path=f"/schedules/{SCHEDULE_ID}/runs")

        with pytest.raises(ScheduleNotFoundError):
            await service.list_runs(SCHEDULE_ID, "mock_user_id", paginator)

    async def test_calls_repo_with_pagination_params(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule = make_schedule(mocker)
        schedule_repo.get.return_value = (schedule, None)
        run_repo.list_for_schedule.return_value = (0, [])

        service = ScheduleRunService(schedule_repo, run_repo)
        paginator = Paginator(limit=5, offset=10, base_path=f"/schedules/{SCHEDULE_ID}/runs")
        await service.list_runs(SCHEDULE_ID, "mock_user_id", paginator)

        run_repo.list_for_schedule.assert_awaited_once_with(SCHEDULE_ID, limit=5, offset=10)

    async def test_maps_created_at_to_start_time(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule = make_schedule(mocker)
        run = make_run(mocker)
        expected_start = run.created_at
        schedule_repo.get.return_value = (schedule, None)
        run_repo.list_for_schedule.return_value = (1, [run])

        service = ScheduleRunService(schedule_repo, run_repo)
        paginator = Paginator(limit=10, offset=0, base_path=f"/schedules/{SCHEDULE_ID}/runs")
        result = await service.list_runs(SCHEDULE_ID, "mock_user_id", paginator)

        assert result.results[0].start_time == expected_start

    async def test_empty_list_returns_zero_count(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule = make_schedule(mocker)
        schedule_repo.get.return_value = (schedule, None)
        run_repo.list_for_schedule.return_value = (0, [])

        service = ScheduleRunService(schedule_repo, run_repo)
        paginator = Paginator(limit=10, offset=0, base_path=f"/schedules/{SCHEDULE_ID}/runs")
        result = await service.list_runs(SCHEDULE_ID, "mock_user_id", paginator)

        assert result.count == 0
        assert result.results == []


class TestScheduleRunServiceGetRun:
    async def test_returns_run_response(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule = make_schedule(mocker)
        run = make_run(mocker)
        schedule_repo.get.return_value = (schedule, None)
        run_repo.get_for_schedule.return_value = run

        service = ScheduleRunService(schedule_repo, run_repo)
        result = await service.get_run(SCHEDULE_ID, "mock_user_id", RUN_ID)

        assert result.id == run.id
        assert result.status == run.status

    async def test_raises_schedule_not_found_when_schedule_missing(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule_repo.get.return_value = None

        service = ScheduleRunService(schedule_repo, run_repo)

        with pytest.raises(ScheduleNotFoundError):
            await service.get_run(SCHEDULE_ID, "mock_user_id", RUN_ID)

    async def test_raises_run_not_found_when_run_missing(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule = make_schedule(mocker)
        schedule_repo.get.return_value = (schedule, None)
        run_repo.get_for_schedule.return_value = None

        service = ScheduleRunService(schedule_repo, run_repo)

        with pytest.raises(ScheduleRunNotFoundError):
            await service.get_run(SCHEDULE_ID, "mock_user_id", RUN_ID)

    async def test_embeds_schedule_summary(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule = make_schedule(mocker)
        run = make_run(mocker)
        schedule_repo.get.return_value = (schedule, None)
        run_repo.get_for_schedule.return_value = run

        service = ScheduleRunService(schedule_repo, run_repo)
        result = await service.get_run(SCHEDULE_ID, "mock_user_id", RUN_ID)

        assert result.schedule.display_name == schedule.display_name
        assert result.schedule.trigger == schedule.trigger
        assert result.schedule.properties == schedule.kwargs

    async def test_run_not_found_when_repo_returns_none(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule = make_schedule(mocker)
        schedule_repo.get.return_value = (schedule, None)
        run_repo.get_for_schedule.return_value = None

        service = ScheduleRunService(schedule_repo, run_repo)

        with pytest.raises(ScheduleRunNotFoundError):
            await service.get_run(SCHEDULE_ID, "mock_user_id", str(uuid.uuid4()))

    async def test_error_run_result_contains_error_text(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule = make_schedule(mocker)
        run = make_run(mocker)
        run.status = ScheduleRunStatus.ERROR
        run.result = {"error_text": "timeout"}
        schedule_repo.get.return_value = (schedule, None)
        run_repo.get_for_schedule.return_value = run

        service = ScheduleRunService(schedule_repo, run_repo)
        result = await service.get_run(SCHEDULE_ID, "mock_user_id", RUN_ID)

        assert result.result == {"error_text": "timeout"}
        assert result.status == ScheduleRunStatus.ERROR

    async def test_in_progress_run_has_null_end_time_and_result(self, mocker):
        from src.lib.services.schedule_run_service import ScheduleRunService

        schedule_repo = AsyncMock()
        run_repo = AsyncMock()
        schedule = make_schedule(mocker)
        run = make_run(mocker)
        run.status = ScheduleRunStatus.IN_PROGRESS
        run.end_time = None
        run.result = None
        schedule_repo.get.return_value = (schedule, None)
        run_repo.get_for_schedule.return_value = run

        service = ScheduleRunService(schedule_repo, run_repo)
        result = await service.get_run(SCHEDULE_ID, "mock_user_id", RUN_ID)

        assert result.end_time is None
        assert result.result is None
        assert result.status == ScheduleRunStatus.IN_PROGRESS

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.lib.db.enums import TargetType, TriggerType
from src.lib.exceptions.errors import JobRegistrationError, ScheduleNotFoundError
from src.lib.models.schedule import Schedule
from src.lib.schemas.pagination import Page, Paginator
from src.lib.schemas.schedule import (
    CronFields,
    CronTriggerSpec,
    ScheduleProperties,
    ScheduleRequest,
    ScheduleResponse,
    ScheduleTrigger,
)
from src.lib.services.schedule_service import ScheduleService


def make_request() -> ScheduleRequest:
    return ScheduleRequest(
        display_name="Test Schedule",
        service_id="dial-oauth",
        trigger=ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9))),
        properties=ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://core.dial/openai/",
            payload={"model": "gpt-4o"},
            api_version="2025-01-01-preview",
        ),
    )


def make_schedule(schedule_id: str = "test-uuid") -> Schedule:
    return Schedule(
        id=schedule_id,
        display_name="Test Schedule",
        created_by="mock_user_id",
        service_id="dial-oauth",
        trigger={"cron": {"fields": {"hour": 9}}},
        kwargs={"target_type": "chat_completion", "url": "https://example.com"},
    )


def make_request_with_metadata(metadata: dict) -> ScheduleRequest:
    return ScheduleRequest(
        display_name="Test Schedule",
        service_id="dial-oauth",
        trigger=ScheduleTrigger(cron=CronTriggerSpec(fields=CronFields(hour=9))),
        properties=ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://core.dial/openai/",
            payload={"model": "gpt-4o"},
            api_version="2025-01-01-preview",
        ),
        metadata=metadata,
    )


def make_date_request() -> ScheduleRequest:
    """Request with a date trigger to verify JSONB datetime serialization."""
    from datetime import timezone

    return ScheduleRequest(
        display_name="Date Schedule",
        service_id="dial-oauth",
        trigger=ScheduleTrigger(date=datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc)),
        properties=ScheduleProperties(
            target_type=TargetType.CHAT_COMPLETION,
            url="https://core.dial/openai/",
            payload={"model": "gpt-4o"},
            api_version="2025-01-01-preview",
        ),
    )


class TestScheduleServiceCreate:
    @pytest.mark.asyncio
    async def test_create_success(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.create.return_value = schedule
        repo.get.return_value = (schedule, now)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            result = await service.create(make_request(), "mock_user_id")

        assert isinstance(result, ScheduleResponse)
        repo.create.assert_awaited_once()
        repo.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_serializes_trigger_datetimes_as_iso_strings(self):
        """Verifies C1: model_dump(mode='json') so JSONB never receives raw datetimes."""
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.create.return_value = schedule
        repo.get.return_value = (schedule, now)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.create(make_date_request(), "mock_user_id")

        persisted: Schedule = repo.create.await_args[0][0]
        assert isinstance(persisted.trigger["date"], str), "trigger.date must be an ISO string, not a datetime"

    @pytest.mark.asyncio
    async def test_create_rollback_on_job_failure(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        schedule = make_schedule()
        repo.create.return_value = schedule

        service = ScheduleService(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=Exception("add_job failed")),
        ):
            with pytest.raises(JobRegistrationError):
                await service.create(make_request(), "mock_user_id")

        repo.delete.assert_awaited_once()


class TestScheduleServiceServiceId:
    @pytest.mark.asyncio
    async def test_create_persists_service_id(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.create.return_value = schedule
        repo.get.return_value = (schedule, now)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.create(make_request(), "mock_user_id")

        persisted: Schedule = repo.create.await_args[0][0]
        assert persisted.service_id == "dial-oauth"

    @pytest.mark.asyncio
    async def test_create_response_includes_service_id(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.service_id = "dial-api-key"
        schedule.created_at = now
        schedule.updated_at = now
        repo.create.return_value = schedule
        repo.get.return_value = (schedule, now)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            result = await service.create(make_request(), "mock_user_id")

        assert result.service_id == "dial-api-key"

    @pytest.mark.asyncio
    async def test_update_passes_service_id_to_repo(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)
        repo.update.return_value = schedule

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.update("test-uuid", make_request(), "mock_user_id")

        assert repo.update.await_args.kwargs["service_id"] == "dial-oauth"

    @pytest.mark.asyncio
    async def test_update_restores_prior_service_id_on_job_failure(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.service_id = "old-service"
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)
        repo.update.return_value = schedule

        service = ScheduleService(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=Exception("add_job failed")),
        ):
            with pytest.raises(JobRegistrationError):
                await service.update("test-uuid", make_request(), "mock_user_id")

        restore_kwargs = repo.update.await_args_list[1].kwargs
        assert restore_kwargs["service_id"] == "old-service"

    @pytest.mark.asyncio
    async def test_list_includes_service_id(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.service_id = "dial-api-key"
        schedule.created_at = now
        schedule.updated_at = now
        repo.list.return_value = (1, [(schedule, now)])

        service = ScheduleService(repo, scheduler)
        result = await service.list("mock_user_id", Paginator(limit=10, offset=0, base_path="/schedules"))

        assert result.results[0].service_id == "dial-api-key"


class TestScheduleServiceUpdate:
    @pytest.mark.asyncio
    async def test_update_success(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)
        repo.update.return_value = schedule

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            result = await service.update("test-uuid", make_request(), "mock_user_id")

        assert isinstance(result, ScheduleResponse)

    @pytest.mark.asyncio
    async def test_update_raises_not_found_when_missing(self):
        repo = AsyncMock()
        repo.get.return_value = None
        scheduler = MagicMock()
        service = ScheduleService(repo, scheduler)

        with pytest.raises(ScheduleNotFoundError):
            await service.update("nonexistent", make_request(), "mock_user_id")

    @pytest.mark.asyncio
    async def test_update_restores_on_job_failure(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.display_name = "Old Name"
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)
        repo.update.return_value = schedule

        service = ScheduleService(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=Exception("add_job failed")),
        ):
            with pytest.raises(JobRegistrationError):
                await service.update("test-uuid", make_request(), "mock_user_id")

        # update should be called twice: once to update, once to restore
        assert repo.update.await_count == 2

    @pytest.mark.asyncio
    async def test_update_restores_updated_at_on_job_failure(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)
        repo.update.return_value = schedule

        service = ScheduleService(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=Exception("add_job failed")),
        ):
            with pytest.raises(JobRegistrationError):
                await service.update("test-uuid", make_request(), "mock_user_id")

        # second call (restore) must include the prior updated_at
        restore_call_kwargs = repo.update.await_args_list[1].kwargs
        assert restore_call_kwargs["updated_at"] == now

    @pytest.mark.asyncio
    async def test_update_raises_job_error_even_when_restore_fails(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)
        # first update succeeds, restore (second update) fails
        repo.update.side_effect = [schedule, Exception("restore failed")]

        service = ScheduleService(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=Exception("add_job failed")),
        ):
            with pytest.raises(JobRegistrationError):
                await service.update("test-uuid", make_request(), "mock_user_id")


class TestScheduleServiceList:
    @pytest.mark.asyncio
    async def test_list_builds_paginated_response(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.list.return_value = (3, [(schedule, now)])

        service = ScheduleService(repo, scheduler)
        result = await service.list("mock_user_id", Paginator(limit=2, offset=0, base_path="/schedules"))

        assert isinstance(result, Page)
        assert result.count == 3
        assert result.limit == 2
        assert len(result.results) == 1

    @pytest.mark.asyncio
    async def test_list_propagates_links_from_paginator(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.list.return_value = (10, [(schedule, now)])

        service = ScheduleService(repo, scheduler)
        result = await service.list("mock_user_id", Paginator(limit=2, offset=0, base_path="/schedules"))

        assert result.next == "/schedules?limit=2&offset=2"
        assert result.previous is None

    @pytest.mark.asyncio
    async def test_list_includes_display_name(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.list.return_value = (1, [(schedule, now)])

        service = ScheduleService(repo, scheduler)
        result = await service.list("mock_user_id", Paginator(limit=10, offset=0, base_path="/schedules"))

        assert result.results[0].display_name == schedule.display_name

    @pytest.mark.asyncio
    async def test_list_derives_trigger_type(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.trigger = {"cron": {"fields": {"hour": 9}}}
        schedule.created_at = now
        schedule.updated_at = now
        repo.list.return_value = (1, [(schedule, now)])

        service = ScheduleService(repo, scheduler)
        result = await service.list("mock_user_id", Paginator(limit=10, offset=0, base_path="/schedules"))

        assert result.results[0].trigger_type == TriggerType.CRON


class TestScheduleServiceMetadata:
    @pytest.mark.asyncio
    async def test_create_persists_metadata(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.create.return_value = schedule
        repo.get.return_value = (schedule, now)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.create(make_request_with_metadata({"tenant": "hcscd"}), "mock_user_id")

        persisted: Schedule = repo.create.await_args[0][0]
        assert persisted.schedule_metadata == {"tenant": "hcscd"}

    @pytest.mark.asyncio
    async def test_create_defaults_metadata_to_empty(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.create.return_value = schedule
        repo.get.return_value = (schedule, now)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.create(make_request(), "mock_user_id")

        persisted: Schedule = repo.create.await_args[0][0]
        assert persisted.schedule_metadata == {}

    @pytest.mark.asyncio
    async def test_update_passes_metadata_to_repo(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)
        repo.update.return_value = schedule

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.update("test-uuid", make_request_with_metadata({"env": "prod"}), "mock_user_id")

        assert repo.update.await_args.kwargs["metadata"] == {"env": "prod"}

    @pytest.mark.asyncio
    async def test_update_restores_prior_metadata_on_job_failure(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.schedule_metadata = {"tenant": "old"}
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)
        repo.update.return_value = schedule

        service = ScheduleService(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=Exception("add_job failed")),
        ):
            with pytest.raises(JobRegistrationError):
                await service.update("test-uuid", make_request_with_metadata({"tenant": "new"}), "mock_user_id")

        restore_kwargs = repo.update.await_args_list[1].kwargs
        assert restore_kwargs["metadata"] == {"tenant": "old"}

    @pytest.mark.asyncio
    async def test_list_forwards_metadata_filter_to_repo(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.list.return_value = (1, [(schedule, now)])

        service = ScheduleService(repo, scheduler)
        await service.list(
            "mock_user_id",
            Paginator(limit=10, offset=0, base_path="/schedules"),
            {"tenant": "hcscd"},
        )

        assert repo.list.await_args.kwargs["metadata_filter"] == {"tenant": "hcscd"}

    @pytest.mark.asyncio
    async def test_get_returns_metadata_in_response(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.schedule_metadata = {"tenant": "hcscd"}
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)

        service = ScheduleService(repo, scheduler)
        result = await service.get("test-uuid", "mock_user_id")

        assert result.metadata == {"tenant": "hcscd"}


class TestScheduleServiceGet:
    @pytest.mark.asyncio
    async def test_get_returns_response(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)

        service = ScheduleService(repo, scheduler)
        result = await service.get("test-uuid", "mock_user_id")

        assert isinstance(result, ScheduleResponse)
        assert result.id == "test-uuid"

    @pytest.mark.asyncio
    async def test_get_raises_not_found_when_absent(self):
        repo = AsyncMock()
        repo.get.return_value = None
        scheduler = MagicMock()

        service = ScheduleService(repo, scheduler)

        with pytest.raises(ScheduleNotFoundError):
            await service.get("nonexistent", "mock_user_id")


class TestScheduleServicePause:
    @pytest.mark.asyncio
    async def test_pause_owned_calls_pause_job_and_returns_response(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, None)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()) as mock_thread:
            result = await service.pause("test-uuid", "mock_user_id")

        assert isinstance(result, ScheduleResponse)
        assert result.next_run_time is None
        mock_thread.assert_awaited_once()
        call_args = mock_thread.await_args[0]
        assert call_args[0] == scheduler.pause_job
        assert call_args[1] == "test-uuid"

    @pytest.mark.asyncio
    async def test_pause_idempotent_already_paused(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, None)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            result = await service.pause("test-uuid", "mock_user_id")

        assert isinstance(result, ScheduleResponse)

    @pytest.mark.asyncio
    async def test_pause_job_lookup_error_raises_schedule_not_found(self):
        from apscheduler.jobstores.base import JobLookupError

        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, None)

        service = ScheduleService(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=JobLookupError("test-uuid")),
        ):
            with pytest.raises(ScheduleNotFoundError):
                await service.pause("test-uuid", "mock_user_id")

    @pytest.mark.asyncio
    async def test_pause_not_owned_raises_schedule_not_found(self):
        repo = AsyncMock()
        repo.get.return_value = None
        scheduler = MagicMock()

        service = ScheduleService(repo, scheduler)

        with pytest.raises(ScheduleNotFoundError):
            await service.pause("test-uuid", "other_user")

        scheduler.pause_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_pause_runtime_error_propagates(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, None)

        service = ScheduleService(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("unexpected")),
        ):
            with pytest.raises(RuntimeError):
                await service.pause("test-uuid", "mock_user_id")


class TestScheduleServiceResume:
    @pytest.mark.asyncio
    async def test_resume_owned_calls_resume_job_and_returns_response(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()) as mock_thread:
            result = await service.resume("test-uuid", "mock_user_id")

        assert isinstance(result, ScheduleResponse)
        assert result.next_run_time == now
        mock_thread.assert_awaited_once()
        call_args = mock_thread.await_args[0]
        assert call_args[0] == scheduler.resume_job
        assert call_args[1] == "test-uuid"

    @pytest.mark.asyncio
    async def test_resume_idempotent_already_active(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            result = await service.resume("test-uuid", "mock_user_id")

        assert isinstance(result, ScheduleResponse)

    @pytest.mark.asyncio
    async def test_resume_exhausted_trigger_returns_null_next_run_time(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, None)

        service = ScheduleService(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            result = await service.resume("test-uuid", "mock_user_id")

        assert result.next_run_time is None

    @pytest.mark.asyncio
    async def test_resume_job_lookup_error_raises_schedule_not_found(self):
        from apscheduler.jobstores.base import JobLookupError

        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, None)

        service = ScheduleService(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=JobLookupError("test-uuid")),
        ):
            with pytest.raises(ScheduleNotFoundError):
                await service.resume("test-uuid", "mock_user_id")

    @pytest.mark.asyncio
    async def test_resume_not_owned_raises_schedule_not_found(self):
        repo = AsyncMock()
        repo.get.return_value = None
        scheduler = MagicMock()

        service = ScheduleService(repo, scheduler)

        with pytest.raises(ScheduleNotFoundError):
            await service.resume("test-uuid", "other_user")

        scheduler.resume_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_runtime_error_propagates(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, None)

        service = ScheduleService(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("unexpected")),
        ):
            with pytest.raises(RuntimeError):
                await service.resume("test-uuid", "mock_user_id")

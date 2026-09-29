from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from src.lib.db.enums import TargetType, TriggerType
from src.lib.exceptions.errors import (
    JobRegistrationError,
    JobRemovalError,
    ScheduleAlreadyDeletedError,
    ScheduleNotFoundError,
)
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


def make_service(repo, scheduler, run_repo=None) -> ScheduleService:
    """Build a ScheduleService, defaulting the run repository to a no-runs stub.

    Tests that care about run history pass an explicit ``run_repo``; every other test gets a
    stub reporting zero runs, which is the pre-existing behaviour for schedules with no history.
    """
    if run_repo is None:
        run_repo = AsyncMock()
        run_repo.count_for_schedule.return_value = 0
    return ScheduleService(repo, run_repo, scheduler)


def make_request() -> ScheduleRequest:
    return ScheduleRequest(
        display_name="Test Schedule",
        service_id="dial-native",
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
        service_id="dial-native",
        trigger={"cron": {"fields": {"hour": 9}}},
        kwargs={"target_type": "chat_completion", "url": "https://example.com"},
    )


def make_request_with_metadata(metadata: dict) -> ScheduleRequest:
    return ScheduleRequest(
        display_name="Test Schedule",
        service_id="dial-native",
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
        service_id="dial-native",
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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.create(make_request(), "mock_user_id")

        persisted: Schedule = repo.create.await_args[0][0]
        assert persisted.service_id == "dial-native"

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.update("test-uuid", make_request(), "mock_user_id")

        assert repo.update.await_args.kwargs["service_id"] == "dial-native"

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)
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

        service = make_service(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            result = await service.update("test-uuid", make_request(), "mock_user_id")

        assert isinstance(result, ScheduleResponse)

    @pytest.mark.asyncio
    async def test_update_raises_not_found_when_missing(self):
        repo = AsyncMock()
        repo.get.return_value = None
        scheduler = MagicMock()
        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)
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

        service = make_service(repo, scheduler)
        result = await service.list("mock_user_id", Paginator(limit=2, offset=0, base_path="/schedules"))

        assert result.next == "/schedules?limit=2&offset=2&order_by=created_at&order_dir=desc"
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

        service = make_service(repo, scheduler)
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

        service = make_service(repo, scheduler)
        result = await service.list("mock_user_id", Paginator(limit=10, offset=0, base_path="/schedules"))

        assert result.results[0].trigger_type == TriggerType.CRON


class TestScheduleServiceDescription:
    @pytest.mark.asyncio
    async def test_create_persists_description(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.create.return_value = schedule
        repo.get.return_value = (schedule, now)

        request = ScheduleRequest(**{**make_request().model_dump(), "description": "ops notes"})
        service = make_service(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.create(request, "mock_user_id")

        persisted: Schedule = repo.create.await_args[0][0]
        assert persisted.description == "ops notes"

    @pytest.mark.asyncio
    async def test_update_passes_description_to_repo(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)
        repo.update.return_value = schedule

        request = ScheduleRequest(**{**make_request().model_dump(), "description": "new notes"})
        service = make_service(repo, scheduler)

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.update("test-uuid", request, "mock_user_id")

        assert repo.update.await_args.kwargs["description"] == "new notes"

    @pytest.mark.asyncio
    async def test_update_restores_prior_description_on_job_failure(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.description = "old notes"
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)
        repo.update.return_value = schedule

        request = ScheduleRequest(**{**make_request().model_dump(), "description": "new notes"})
        service = make_service(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=Exception("add_job failed")),
        ):
            with pytest.raises(JobRegistrationError):
                await service.update("test-uuid", request, "mock_user_id")

        restore_kwargs = repo.update.await_args_list[1].kwargs
        assert restore_kwargs["description"] == "old notes"

    @pytest.mark.asyncio
    async def test_get_returns_description_in_response(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.description = "detail notes"
        schedule.created_at = now
        schedule.updated_at = now
        repo.get.return_value = (schedule, now)

        service = make_service(repo, scheduler)
        result = await service.get("test-uuid", "mock_user_id")

        assert result.description == "detail notes"

    @pytest.mark.asyncio
    async def test_list_includes_description(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.description = "row notes"
        schedule.created_at = now
        schedule.updated_at = now
        repo.list.return_value = (1, [(schedule, now)])

        service = make_service(repo, scheduler)
        result = await service.list("mock_user_id", Paginator(limit=10, offset=0, base_path="/schedules"))

        assert result.results[0].description == "row notes"

    @pytest.mark.asyncio
    async def test_list_description_none_when_absent(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.description = None
        schedule.created_at = now
        schedule.updated_at = now
        repo.list.return_value = (1, [(schedule, now)])

        service = make_service(repo, scheduler)
        result = await service.list("mock_user_id", Paginator(limit=10, offset=0, base_path="/schedules"))

        assert result.results[0].description is None


class TestScheduleServiceListTrigger:
    @pytest.mark.asyncio
    async def test_list_includes_full_trigger(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.trigger = {"cron": {"fields": {"hour": 9}}}
        schedule.created_at = now
        schedule.updated_at = now
        repo.list.return_value = (1, [(schedule, now)])

        service = make_service(repo, scheduler)
        result = await service.list("mock_user_id", Paginator(limit=10, offset=0, base_path="/schedules"))

        item = result.results[0]
        assert item.trigger == {"cron": {"fields": {"hour": 9}}}
        assert item.trigger_type == TriggerType.CRON

    @pytest.mark.asyncio
    async def test_list_forwards_name_filter_to_repo(self):
        repo = AsyncMock()
        scheduler = MagicMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        repo.list.return_value = (1, [(schedule, now)])

        service = make_service(repo, scheduler)
        await service.list(
            "mock_user_id",
            Paginator(limit=10, offset=0, base_path="/schedules"),
            name_filter="report",
        )

        assert repo.list.await_args.kwargs["name_filter"] == "report"


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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)
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

        service = make_service(repo, scheduler)
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

        service = make_service(repo, scheduler)
        result = await service.get("test-uuid", "mock_user_id")

        assert isinstance(result, ScheduleResponse)
        assert result.id == "test-uuid"

    @pytest.mark.asyncio
    async def test_get_raises_not_found_when_absent(self):
        repo = AsyncMock()
        repo.get.return_value = None
        scheduler = MagicMock()

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

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

        service = make_service(repo, scheduler)

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("unexpected")),
        ):
            with pytest.raises(RuntimeError):
                await service.resume("test-uuid", "mock_user_id")


def make_live_schedule() -> Schedule:
    """An owned, not-deleted schedule with timestamps set."""
    schedule = make_schedule()
    schedule.created_at = schedule.updated_at = datetime.now(UTC)
    return schedule


def make_deleted_schedule() -> Schedule:
    """An owned, soft-deleted schedule with timestamps set."""
    schedule = make_live_schedule()
    schedule.is_deleted = True
    return schedule


def _live_repo() -> AsyncMock:
    """Repository stub returning one owned, live schedule with no next run time."""
    repo = AsyncMock()
    schedule = make_live_schedule()
    repo.get.return_value = (schedule, None)
    repo.delete_owned.return_value = True
    repo.mark_deleted.return_value = True
    return repo


def _run_repo(count: int) -> AsyncMock:
    run_repo = AsyncMock()
    run_repo.count_for_schedule.return_value = count
    return run_repo


class TestScheduleServiceDeleteNoRuns:
    """Zero run history: unregister the job, then remove the row outright."""

    @pytest.mark.asyncio
    async def test_removes_job_then_row(self):
        repo = _live_repo()
        scheduler = MagicMock()
        service = make_service(repo, scheduler, _run_repo(0))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()) as mock_thread:
            result = await service.delete("test-uuid", "mock_user_id")

        assert result is None
        mock_thread.assert_awaited_once()
        call_args = mock_thread.await_args[0]
        assert call_args[0] == scheduler.remove_job
        assert call_args[1] == "test-uuid"
        repo.delete_owned.assert_awaited_once_with("test-uuid", "mock_user_id")
        repo.mark_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unregisters_before_persisting(self):
        """FR-017: a 204 must guarantee the schedule cannot fire, so the job goes first."""
        repo = _live_repo()
        order: list[str] = []

        async def record_persist(*_args):
            order.append("persist")
            return True

        repo.delete_owned = AsyncMock(side_effect=record_persist)
        service = make_service(repo, MagicMock(), _run_repo(0))

        async def record_thread(*_args, **_kwargs):
            order.append("unregister")

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=record_thread):
            await service.delete("test-uuid", "mock_user_id")

        assert order == ["unregister", "persist"]

    @pytest.mark.asyncio
    async def test_job_lookup_error_still_completes_delete(self):
        """A missing job means already unregistered — the delete must still finish."""
        from apscheduler.jobstores.base import JobLookupError

        repo = _live_repo()
        service = make_service(repo, MagicMock(), _run_repo(0))

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=JobLookupError("gone")),
        ):
            await service.delete("test-uuid", "mock_user_id")

        repo.delete_owned.assert_awaited_once_with("test-uuid", "mock_user_id")

    @pytest.mark.asyncio
    async def test_scheduler_failure_raises_job_removal_error_without_db_write(self):
        repo = _live_repo()
        service = make_service(repo, MagicMock(), _run_repo(0))

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("job store unreachable")),
        ):
            with pytest.raises(JobRemovalError):
                await service.delete("test-uuid", "mock_user_id")

        repo.delete_owned.assert_not_awaited()
        repo.mark_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_persist_failure_propagates_then_retry_succeeds(self):
        """Job removed but row not written: the caller errors, and a retried delete completes."""
        from apscheduler.jobstores.base import JobLookupError

        repo = _live_repo()
        repo.delete_owned = AsyncMock(side_effect=[RuntimeError("db down"), True])
        service = make_service(repo, MagicMock(), _run_repo(0))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            with pytest.raises(RuntimeError):
                await service.delete("test-uuid", "mock_user_id")

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=JobLookupError("already gone")),
        ):
            await service.delete("test-uuid", "mock_user_id")

        assert repo.delete_owned.await_count == 2

    @pytest.mark.asyncio
    async def test_lost_race_to_a_soft_delete_raises_already_deleted(self):
        repo = _live_repo()
        repo.delete_owned.return_value = False
        deleted = make_schedule()
        deleted.is_deleted = True
        deleted.created_at = deleted.updated_at = datetime.now(UTC)
        # First read sees a live schedule; the post-race re-read sees the winner's soft delete.
        repo.get = AsyncMock(side_effect=[(make_live_schedule(), None), (deleted, None)])
        service = make_service(repo, MagicMock(), _run_repo(0))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            with pytest.raises(ScheduleAlreadyDeletedError):
                await service.delete("test-uuid", "mock_user_id")

    @pytest.mark.asyncio
    async def test_lost_race_to_a_hard_delete_raises_not_found(self):
        """409 would contradict the contract: the row is gone, so the answer is 404."""
        repo = _live_repo()
        repo.delete_owned.return_value = False
        repo.get = AsyncMock(side_effect=[(make_live_schedule(), None), None])
        service = make_service(repo, MagicMock(), _run_repo(0))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            with pytest.raises(ScheduleNotFoundError):
                await service.delete("test-uuid", "mock_user_id")

    @pytest.mark.asyncio
    async def test_not_found_attempt_is_logged(self):
        """FR-018/SC-008: every attempt is traceable, including the rejected ones."""

        repo = AsyncMock()
        repo.get.return_value = None
        service = make_service(repo, MagicMock(), _run_repo(0))

        with structlog.testing.capture_logs() as logs:
            with pytest.raises(ScheduleNotFoundError):
                await service.delete("missing", "mock_user_id")

        events = [log for log in logs if log.get("event") == "schedule_delete_not_found"]
        assert len(events) == 1
        assert events[0]["reason"] == "not_found_or_not_owned"
        assert events[0]["schedule_id"] == "missing"

    @pytest.mark.asyncio
    async def test_stopped_scheduler_is_rejected_before_any_write(self):
        """A stopped scheduler's remove_job raises JobLookupError without touching the job
        store, so swallowing it would delete the row while the job keeps firing."""
        from apscheduler.schedulers.base import STATE_STOPPED

        repo = _live_repo()
        scheduler = MagicMock()
        scheduler.state = STATE_STOPPED
        service = make_service(repo, scheduler, _run_repo(0))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()) as mock_thread:
            with pytest.raises(JobRemovalError):
                await service.delete("test-uuid", "mock_user_id")

        mock_thread.assert_not_awaited()
        repo.delete_owned.assert_not_awaited()
        repo.mark_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_schedule_raises_not_found_without_touching_scheduler(self):
        repo = AsyncMock()
        repo.get.return_value = None
        service = make_service(repo, MagicMock(), _run_repo(0))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()) as mock_thread:
            with pytest.raises(ScheduleNotFoundError):
                await service.delete("missing", "mock_user_id")

        mock_thread.assert_not_awaited()
        repo.delete_owned.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_foreign_schedule_raises_not_found(self):
        repo = AsyncMock()
        repo.get.return_value = None
        service = make_service(repo, MagicMock(), _run_repo(0))

        with pytest.raises(ScheduleNotFoundError):
            await service.delete("test-uuid", "other_user")


class TestScheduleServiceDeleteWithRuns:
    """Run history exists: unregister the job, keep the row, set is_deleted."""

    @pytest.mark.asyncio
    async def test_marks_deleted_and_keeps_row(self):
        repo = _live_repo()
        scheduler = MagicMock()
        service = make_service(repo, scheduler, _run_repo(3))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()) as mock_thread:
            await service.delete("test-uuid", "mock_user_id")

        assert mock_thread.await_args[0][0] == scheduler.remove_job
        repo.mark_deleted.assert_awaited_once_with("test-uuid", "mock_user_id")
        repo.delete_owned.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_in_progress_run_counts_as_history(self):
        repo = _live_repo()
        service = make_service(repo, MagicMock(), _run_repo(1))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.delete("test-uuid", "mock_user_id")

        repo.mark_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_integrity_error_falls_back_to_soft_delete(self):
        """A run inserted after the count is blocked by the FK; soft-delete instead of failing."""
        from sqlalchemy.exc import IntegrityError

        repo = _live_repo()
        repo.delete_owned = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("FK violation")))
        service = make_service(repo, MagicMock(), _run_repo(0))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            await service.delete("test-uuid", "mock_user_id")

        repo.delete_owned.assert_awaited_once()
        repo.mark_deleted.assert_awaited_once_with("test-uuid", "mock_user_id")

    @pytest.mark.asyncio
    async def test_fallback_recounts_so_the_log_is_not_self_contradictory(self):
        """Logging run_count=0 next to "marked deleted because it has history" is nonsense."""
        from sqlalchemy.exc import IntegrityError

        repo = _live_repo()
        repo.delete_owned = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("FK violation")))
        run_repo = AsyncMock()
        run_repo.count_for_schedule = AsyncMock(side_effect=[0, 1])
        service = make_service(repo, MagicMock(), run_repo)

        with structlog.testing.capture_logs() as logs:
            with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
                await service.delete("test-uuid", "mock_user_id")

        assert run_repo.count_for_schedule.await_count == 2
        marked = [log for log in logs if log.get("event") == "schedule_marked_deleted"]
        assert len(marked) == 1
        assert marked[0]["run_count"] == 1

    @pytest.mark.asyncio
    async def test_fallback_persist_failure_is_logged_as_persist_failed(self):
        """The worst case on this path — job gone, DELETE blocked, UPDATE fails — needs the log."""
        from sqlalchemy.exc import IntegrityError

        repo = _live_repo()
        repo.delete_owned = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("FK violation")))
        repo.mark_deleted = AsyncMock(side_effect=RuntimeError("db down"))
        run_repo = AsyncMock()
        run_repo.count_for_schedule = AsyncMock(side_effect=[0, 1])
        service = make_service(repo, MagicMock(), run_repo)

        with structlog.testing.capture_logs() as logs:
            with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
                with pytest.raises(RuntimeError):
                    await service.delete("test-uuid", "mock_user_id")

        assert any(log.get("event") == "schedule_delete_persist_failed" for log in logs)

    @pytest.mark.asyncio
    async def test_lost_race_on_soft_branch_raises_already_deleted(self):
        repo = _live_repo()
        repo.mark_deleted.return_value = False
        # The post-race re-read must see the winner's soft delete, not a live row.
        repo.get = AsyncMock(side_effect=[(make_live_schedule(), None), (make_deleted_schedule(), None)])
        service = make_service(repo, MagicMock(), _run_repo(2))

        with structlog.testing.capture_logs() as logs:
            with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
                with pytest.raises(ScheduleAlreadyDeletedError):
                    await service.delete("test-uuid", "mock_user_id")

        conflicts = [log for log in logs if log.get("event") == "schedule_delete_conflict"]
        assert len(conflicts) == 1
        assert conflicts[0]["reason"] == "deleted_concurrently"

    @pytest.mark.asyncio
    async def test_lost_race_on_soft_branch_to_a_hard_delete_raises_not_found(self):
        repo = _live_repo()
        repo.mark_deleted.return_value = False
        repo.get = AsyncMock(side_effect=[(make_live_schedule(), None), None])
        service = make_service(repo, MagicMock(), _run_repo(2))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            with pytest.raises(ScheduleNotFoundError):
                await service.delete("test-uuid", "mock_user_id")

    @pytest.mark.asyncio
    async def test_unflagged_row_after_failed_conditional_write_is_flagged_as_unexpected(self):
        """A present, unflagged row contradicts the write's own predicate — never report success."""
        repo = _live_repo()
        repo.mark_deleted.return_value = False
        repo.get = AsyncMock(side_effect=[(make_live_schedule(), None), (make_live_schedule(), None)])
        service = make_service(repo, MagicMock(), _run_repo(2))

        with structlog.testing.capture_logs() as logs:
            with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
                with pytest.raises(ScheduleAlreadyDeletedError):
                    await service.delete("test-uuid", "mock_user_id")

        conflicts = [log for log in logs if log.get("event") == "schedule_delete_conflict"]
        assert conflicts[0]["reason"] == "conditional_write_matched_no_row"
        assert conflicts[0]["log_level"] == "warning"

    @pytest.mark.asyncio
    async def test_fallback_recount_failure_is_logged_as_persist_failed(self):
        from sqlalchemy.exc import IntegrityError

        repo = _live_repo()
        repo.delete_owned = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("FK violation")))
        run_repo = AsyncMock()
        run_repo.count_for_schedule = AsyncMock(side_effect=[0, RuntimeError("db down")])
        service = make_service(repo, MagicMock(), run_repo)

        with structlog.testing.capture_logs() as logs:
            with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
                with pytest.raises(RuntimeError):
                    await service.delete("test-uuid", "mock_user_id")

        assert any(log.get("event") == "schedule_delete_persist_failed" for log in logs)
        repo.mark_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_soft_persist_failure_propagates_then_retry_succeeds(self):
        """Job removed but the flag not set: the caller errors, and a retried delete completes."""
        from apscheduler.jobstores.base import JobLookupError

        repo = _live_repo()
        repo.mark_deleted = AsyncMock(side_effect=[RuntimeError("db down"), True])
        service = make_service(repo, MagicMock(), _run_repo(2))

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()):
            with pytest.raises(RuntimeError):
                await service.delete("test-uuid", "mock_user_id")

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=JobLookupError("already gone")),
        ):
            await service.delete("test-uuid", "mock_user_id")

        assert repo.mark_deleted.await_count == 2

    @pytest.mark.asyncio
    async def test_scheduler_failure_leaves_row_untouched(self):
        repo = _live_repo()
        service = make_service(repo, MagicMock(), _run_repo(2))

        with patch(
            "src.lib.services.schedule_service.asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(JobRemovalError):
                await service.delete("test-uuid", "mock_user_id")

        repo.mark_deleted.assert_not_awaited()


class TestScheduleServiceSoftDeletedIsFrozen:
    """A soft-deleted schedule rejects every mutating operation (FR-013, FR-015)."""

    @staticmethod
    def _deleted_repo() -> AsyncMock:
        repo = AsyncMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        schedule.is_deleted = True
        repo.get.return_value = (schedule, None)
        return repo

    @pytest.mark.asyncio
    async def test_delete_raises_already_deleted(self):
        repo = self._deleted_repo()
        service = make_service(repo, MagicMock())

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()) as mock_thread:
            with pytest.raises(ScheduleAlreadyDeletedError):
                await service.delete("test-uuid", "mock_user_id")

        mock_thread.assert_not_awaited()
        repo.delete_owned.assert_not_awaited()
        repo.mark_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_raises_already_deleted(self):
        repo = self._deleted_repo()
        service = make_service(repo, MagicMock())

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()) as mock_thread:
            with pytest.raises(ScheduleAlreadyDeletedError):
                await service.update("test-uuid", make_request(), "mock_user_id")

        mock_thread.assert_not_awaited()
        repo.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pause_raises_already_deleted(self):
        repo = self._deleted_repo()
        service = make_service(repo, MagicMock())

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()) as mock_thread:
            with pytest.raises(ScheduleAlreadyDeletedError):
                await service.pause("test-uuid", "mock_user_id")

        mock_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_raises_already_deleted(self):
        repo = self._deleted_repo()
        service = make_service(repo, MagicMock())

        with patch("src.lib.services.schedule_service.asyncio.to_thread", new=AsyncMock()) as mock_thread:
            with pytest.raises(ScheduleAlreadyDeletedError):
                await service.resume("test-uuid", "mock_user_id")

        mock_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_still_returns_soft_deleted_schedule(self):
        """Details stay readable so the owner can see the flag and reach run history."""
        repo = self._deleted_repo()
        service = make_service(repo, MagicMock())

        result = await service.get("test-uuid", "mock_user_id")

        assert result.is_deleted is True
        assert result.next_run_time is None


class TestScheduleServiceListDeletedFilter:
    @pytest.mark.asyncio
    async def test_defaults_to_live_schedules(self):
        repo = AsyncMock()
        repo.list.return_value = (0, [])
        service = make_service(repo, MagicMock())

        await service.list("mock_user_id", Paginator(limit=10, offset=0, base_path="/schedules"))

        assert repo.list.await_args.kwargs["is_deleted"] is False

    @pytest.mark.asyncio
    async def test_forwards_is_deleted_true(self):
        repo = AsyncMock()
        repo.list.return_value = (0, [])
        service = make_service(repo, MagicMock())

        await service.list(
            "mock_user_id",
            Paginator(limit=10, offset=0, base_path="/schedules"),
            is_deleted=True,
        )

        assert repo.list.await_args.kwargs["is_deleted"] is True

    @pytest.mark.asyncio
    async def test_pagination_links_preserve_is_deleted_true(self):
        repo = AsyncMock()
        repo.list.return_value = (50, [])
        service = make_service(repo, MagicMock())

        page = await service.list(
            "mock_user_id",
            Paginator(limit=10, offset=10, base_path="/schedules"),
            is_deleted=True,
        )

        assert "is_deleted=true" in page.next
        assert "is_deleted=true" in page.previous
        assert "order_by=created_at" in page.next

    @pytest.mark.asyncio
    async def test_pagination_links_omit_default_is_deleted(self):
        repo = AsyncMock()
        repo.list.return_value = (50, [])
        service = make_service(repo, MagicMock())

        page = await service.list(
            "mock_user_id",
            Paginator(limit=10, offset=10, base_path="/schedules"),
            is_deleted=False,
        )

        assert "is_deleted" not in page.next
        assert "is_deleted" not in page.previous

    @pytest.mark.asyncio
    async def test_list_items_expose_is_deleted(self):
        repo = AsyncMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = now
        schedule.updated_at = now
        schedule.is_deleted = True
        repo.list.return_value = (1, [(schedule, None)])
        service = make_service(repo, MagicMock())

        page = await service.list(
            "mock_user_id",
            Paginator(limit=10, offset=0, base_path="/schedules"),
            is_deleted=True,
        )

        assert page.results[0].is_deleted is True


class TestScheduleServiceListModel:
    """The model is a projection of the stored payload, not an input the service computes."""

    @pytest.mark.asyncio
    async def test_list_exposes_the_model_from_the_row(self):
        repo = AsyncMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = schedule.updated_at = now
        schedule.model = "gpt-4o"
        repo.list.return_value = (1, [(schedule, now)])

        service = make_service(repo, MagicMock())
        result = await service.list("mock_user_id", Paginator(limit=20, offset=0, base_path="/schedules"))

        assert result.results[0].model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_list_model_is_none_when_the_row_has_none(self):
        """REST and SSE schedules carry no model; the field must be absent, not invented."""
        repo = AsyncMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = schedule.updated_at = now
        schedule.model = None
        repo.list.return_value = (1, [(schedule, now)])

        service = make_service(repo, MagicMock())
        result = await service.list("mock_user_id", Paginator(limit=20, offset=0, base_path="/schedules"))

        assert result.results[0].model is None

    @pytest.mark.asyncio
    async def test_list_forwards_the_model_filter_to_the_repository(self):
        repo = AsyncMock()
        repo.list.return_value = (0, [])

        service = make_service(repo, MagicMock())
        await service.list(
            "mock_user_id",
            Paginator(limit=20, offset=0, base_path="/schedules"),
            model_filter="gpt-4",
        )

        assert repo.list.await_args.kwargs["model_filter"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_list_page_links_carry_the_model_filter(self):
        repo = AsyncMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = schedule.updated_at = now
        repo.list.return_value = (10, [(schedule, now)])

        service = make_service(repo, MagicMock())
        result = await service.list(
            "mock_user_id",
            Paginator(limit=2, offset=0, base_path="/schedules"),
            model_filter="gpt-4",
        )

        assert "model=gpt-4" in result.next

    @pytest.mark.asyncio
    async def test_list_page_links_url_encode_the_model_filter(self):
        """Application deployment ids contain slashes and spaces."""
        repo = AsyncMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = schedule.updated_at = now
        repo.list.return_value = (10, [(schedule, now)])

        service = make_service(repo, MagicMock())
        result = await service.list(
            "mock_user_id",
            Paginator(limit=2, offset=0, base_path="/schedules"),
            model_filter="applications/public/My App__1.0.0",
        )

        assert "model=applications%2Fpublic%2FMy%20App__1.0.0" in result.next

    @pytest.mark.asyncio
    async def test_list_without_a_model_filter_leaves_links_untouched(self):
        repo = AsyncMock()
        now = datetime.now(UTC)
        schedule = make_schedule()
        schedule.created_at = schedule.updated_at = now
        repo.list.return_value = (10, [(schedule, now)])

        service = make_service(repo, MagicMock())
        result = await service.list("mock_user_id", Paginator(limit=2, offset=0, base_path="/schedules"))

        assert "model=" not in result.next

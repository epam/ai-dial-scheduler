import asyncio
from datetime import datetime
from typing import Any
from uuid import uuid4

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.lib.constants import EXECUTE_SCHEDULED_JOB_REF
from src.lib.exceptions.errors import JobRegistrationError, ScheduleNotFoundError
from src.lib.models.schedule import Schedule
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.schemas.pagination import Page, Paginator
from src.lib.schemas.schedule import (
    ScheduleListItem,
    ScheduleRequest,
    ScheduleResponse,
    ScheduleTrigger,
)
from src.lib.services.trigger_factory import build_trigger
from src.lib.utils.logging import logger


class ScheduleService:
    """Business logic for schedule CRUD and APScheduler job registration."""

    def __init__(self, repository: ScheduleRepository, scheduler: AsyncIOScheduler) -> None:
        self._repo = repository
        self._scheduler = scheduler

    async def _register_job(self, schedule_id: str, trigger: ScheduleTrigger, properties: dict[str, Any]) -> None:
        """Register or replace the APScheduler job for a schedule."""
        apscheduler_trigger = build_trigger(trigger)
        kwargs = {**properties, "schedule_id": schedule_id}
        await asyncio.to_thread(
            self._scheduler.add_job,
            EXECUTE_SCHEDULED_JOB_REF,
            apscheduler_trigger,
            id=schedule_id,
            replace_existing=True,
            kwargs=kwargs,
        )

    @staticmethod
    def _to_response(schedule: Schedule, next_run_time: datetime | None) -> ScheduleResponse:
        """Map ORM model + next_run_time to ScheduleResponse."""
        return ScheduleResponse(
            id=schedule.id,
            display_name=schedule.display_name,
            next_run_time=next_run_time,
            trigger=schedule.trigger,
            properties=schedule.kwargs,
            created_by=schedule.created_by,
            created_at=schedule.created_at,
            updated_at=schedule.updated_at,
        )

    async def create(self, request: ScheduleRequest, created_by: str) -> ScheduleResponse:
        """Create a schedule row and register the APScheduler job.

        Rolls back the row if job registration fails (compensating transaction).
        """
        schedule_id = str(uuid4())
        # mode="json" ensures datetime fields are ISO strings, safe for JSONB serialization
        trigger_dict = request.trigger.model_dump(mode="json")
        properties_dict = request.properties.model_dump(mode="json")

        schedule = Schedule(
            id=schedule_id,
            display_name=request.display_name,
            created_by=created_by,
            trigger=trigger_dict,
            kwargs=properties_dict,
        )
        await self._repo.create(schedule)
        logger.info("Schedule persisted", schedule_id=schedule_id)

        try:
            await self._register_job(schedule_id, request.trigger, properties_dict)
            logger.info("Schedule job registered", schedule_id=schedule_id)
        except Exception as exc:
            logger.warning(
                "Job registration failed, rolling back schedule row",
                schedule_id=schedule_id,
                reason=str(exc),
            )
            await self._repo.delete(schedule_id)
            raise JobRegistrationError(str(exc)) from exc

        row = await self._repo.get(schedule_id, created_by)
        schedule, next_run_time = row  # type: ignore[misc]
        return self._to_response(schedule, next_run_time)

    async def update(self, schedule_id: str, request: ScheduleRequest, created_by: str) -> ScheduleResponse:
        """Update a schedule row and re-register the APScheduler job.

        Restores prior row values (including updated_at) if re-registration fails.
        """
        existing = await self._repo.get(schedule_id, created_by)
        if existing is None:
            raise ScheduleNotFoundError(schedule_id)
        prior_schedule, _ = existing

        prior_display_name = prior_schedule.display_name
        prior_trigger = prior_schedule.trigger
        prior_kwargs = prior_schedule.kwargs
        prior_updated_at = prior_schedule.updated_at

        # mode="json" ensures datetime fields are ISO strings, safe for JSONB serialization
        new_trigger_dict = request.trigger.model_dump(mode="json")
        new_properties_dict = request.properties.model_dump(mode="json")

        updated = await self._repo.update(
            schedule_id,
            created_by,
            display_name=request.display_name,
            trigger=new_trigger_dict,
            kwargs=new_properties_dict,
        )
        if updated is None:
            raise ScheduleNotFoundError(schedule_id)

        logger.info("Schedule updated", schedule_id=schedule_id)

        try:
            await self._register_job(schedule_id, request.trigger, new_properties_dict)
            logger.info("Schedule job re-registered", schedule_id=schedule_id)
        except Exception as registration_exc:
            logger.warning(
                "Job re-registration failed, restoring prior schedule values",
                schedule_id=schedule_id,
                reason=str(registration_exc),
            )
            try:
                await self._repo.update(
                    schedule_id,
                    created_by,
                    display_name=prior_display_name,
                    trigger=prior_trigger,
                    kwargs=prior_kwargs,
                    updated_at=prior_updated_at,
                )
            except Exception as restore_exc:
                logger.error(
                    "Schedule row restore failed after job re-registration failure; stores may diverge",
                    schedule_id=schedule_id,
                    restore_reason=str(restore_exc),
                )
            raise JobRegistrationError(str(registration_exc)) from registration_exc

        row = await self._repo.get(schedule_id, created_by)
        schedule, next_run_time = row  # type: ignore[misc]
        return self._to_response(schedule, next_run_time)

    async def list(self, created_by: str, paginator: Paginator) -> Page[ScheduleListItem]:
        """Return a paginated, owner-scoped list of schedule summaries."""
        total, rows = await self._repo.list(created_by, paginator.limit, paginator.offset)

        results = []
        for schedule, next_run_time in rows:
            trigger_type = ScheduleTrigger.model_validate(schedule.trigger).trigger_type
            results.append(
                ScheduleListItem(
                    id=schedule.id,
                    display_name=schedule.display_name,
                    next_run_time=next_run_time,
                    trigger_type=trigger_type,
                    created_by=schedule.created_by,
                    created_at=schedule.created_at,
                    updated_at=schedule.updated_at,
                )
            )

        return paginator.paginate(count=total, results=results)

    async def get(self, schedule_id: str, created_by: str) -> ScheduleResponse:
        """Return the full detail for an owned schedule, or raise ScheduleNotFoundError."""
        row = await self._repo.get(schedule_id, created_by)
        if row is None:
            raise ScheduleNotFoundError(schedule_id)
        schedule, next_run_time = row
        return self._to_response(schedule, next_run_time)

    async def pause(self, schedule_id: str, created_by: str) -> ScheduleResponse:
        """Pause an owned schedule's APScheduler job, or raise ScheduleNotFoundError."""
        row = await self._repo.get(schedule_id, created_by)
        if row is None:
            raise ScheduleNotFoundError(schedule_id)
        try:
            await asyncio.to_thread(self._scheduler.pause_job, schedule_id)
        except JobLookupError as exc:
            raise ScheduleNotFoundError(schedule_id) from exc
        logger.info("schedule_paused", schedule_id=schedule_id)
        row = await self._repo.get(schedule_id, created_by)
        schedule, next_run_time = row  # type: ignore[misc]
        return self._to_response(schedule, next_run_time)

    async def resume(self, schedule_id: str, created_by: str) -> ScheduleResponse:
        """Resume an owned schedule's APScheduler job, or raise ScheduleNotFoundError."""
        row = await self._repo.get(schedule_id, created_by)
        if row is None:
            raise ScheduleNotFoundError(schedule_id)
        try:
            await asyncio.to_thread(self._scheduler.resume_job, schedule_id)
        except JobLookupError as exc:
            raise ScheduleNotFoundError(schedule_id) from exc
        logger.info("schedule_resumed", schedule_id=schedule_id)
        row = await self._repo.get(schedule_id, created_by)
        schedule, next_run_time = row  # type: ignore[misc]
        return self._to_response(schedule, next_run_time)

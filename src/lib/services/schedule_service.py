import asyncio
from datetime import datetime
from typing import Any, NoReturn
from uuid import uuid4

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_STOPPED
from sqlalchemy.exc import IntegrityError

from src.lib.constants import EXECUTE_SCHEDULED_JOB_REF
from src.lib.exceptions.errors import (
    JobRegistrationError,
    JobRemovalError,
    ScheduleAlreadyDeletedError,
    ScheduleNotFoundError,
)
from src.lib.models.schedule import Schedule
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.repositories.schedule_run_repository import ScheduleRunRepository
from src.lib.schemas.ordering import ScheduleOrdering
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

    def __init__(
        self,
        repository: ScheduleRepository,
        run_repository: ScheduleRunRepository,
        scheduler: AsyncIOScheduler,
    ) -> None:
        self._repo = repository
        self._run_repo = run_repository
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
            service_id=schedule.service_id,
            description=schedule.description,
            next_run_time=next_run_time,
            trigger=schedule.trigger,
            properties=schedule.kwargs,
            metadata=schedule.schedule_metadata,
            is_deleted=schedule.is_deleted,
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
            service_id=request.service_id,
            description=request.description,
            trigger=trigger_dict,
            kwargs=properties_dict,
            schedule_metadata=request.metadata,
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
        if prior_schedule.is_deleted:
            raise ScheduleAlreadyDeletedError(schedule_id)

        prior_display_name = prior_schedule.display_name
        prior_service_id = prior_schedule.service_id
        prior_description = prior_schedule.description
        prior_trigger = prior_schedule.trigger
        prior_kwargs = prior_schedule.kwargs
        prior_metadata = prior_schedule.schedule_metadata
        prior_updated_at = prior_schedule.updated_at

        # mode="json" ensures datetime fields are ISO strings, safe for JSONB serialization
        new_trigger_dict = request.trigger.model_dump(mode="json")
        new_properties_dict = request.properties.model_dump(mode="json")

        updated = await self._repo.update(
            schedule_id,
            created_by,
            display_name=request.display_name,
            service_id=request.service_id,
            description=request.description,
            trigger=new_trigger_dict,
            kwargs=new_properties_dict,
            metadata=request.metadata,
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
                    service_id=prior_service_id,
                    description=prior_description,
                    trigger=prior_trigger,
                    kwargs=prior_kwargs,
                    metadata=prior_metadata,
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

    async def _unregister_job(self, schedule_id: str) -> None:
        """Remove a schedule's APScheduler job so it can never fire again.

        A missing job is treated as success: it means the job is already unregistered, which
        happens when a previous delete attempt failed after this step, or when the job was
        never registered. Any other scheduler failure is fatal to the delete, because the
        caller must not be told the schedule is gone while it can still execute.

        A stopped scheduler is rejected up front. ``BaseScheduler.remove_job`` short-circuits
        when the state is ``STATE_STOPPED``: it searches only in-memory pending jobs and raises
        ``JobLookupError`` **without touching the persistent job store**. Swallowing that as
        success would delete the row while the job survives and the worker keeps firing it.

        Raises:
            JobRemovalError: The scheduler is stopped, unreachable, or failed unexpectedly.
        """
        if self._scheduler.state == STATE_STOPPED:
            logger.warning(
                "schedule_delete_job_removal_failed",
                schedule_id=schedule_id,
                reason="scheduler_stopped",
            )
            raise JobRemovalError("scheduler is not running; cannot unregister the job")
        try:
            await asyncio.to_thread(self._scheduler.remove_job, schedule_id)
        except JobLookupError:
            logger.info("schedule_delete_job_absent", schedule_id=schedule_id, reason="job_lookup_error")
            return
        except Exception as exc:
            logger.warning("schedule_delete_job_removal_failed", schedule_id=schedule_id, reason=str(exc))
            raise JobRemovalError(str(exc)) from exc
        logger.info("schedule_delete_job_removed", schedule_id=schedule_id)

    async def _raise_for_lost_race(self, schedule_id: str, created_by: str) -> NoReturn:
        """Raise the right error when a conditional write matched no row.

        Another request won the race. The re-read is owner-scoped, so it tells the two outcomes
        apart: the row being gone means the winner hard-deleted it and the answer is 404, matching
        what a sequential retry returns; the row still being present and flagged means the winner
        soft-deleted it and the answer is 409.

        A present row that is *not* flagged contradicts the conditional write (which matches on
        ``is_deleted = false`` for this same owner, so it would have succeeded). It is logged as an
        unexpected state and still reported as a conflict, because the one thing this must never do
        is report success for a deletion that did not happen.
        """
        row = await self._repo.get(schedule_id, created_by)
        if row is None:
            logger.info("schedule_delete_not_found", schedule_id=schedule_id, reason="deleted_concurrently")
            raise ScheduleNotFoundError(schedule_id)
        schedule, _ = row
        if schedule.is_deleted:
            logger.info("schedule_delete_conflict", schedule_id=schedule_id, reason="deleted_concurrently")
        else:
            logger.warning(
                "schedule_delete_conflict",
                schedule_id=schedule_id,
                reason="conditional_write_matched_no_row",
            )
        raise ScheduleAlreadyDeletedError(schedule_id)

    async def _soft_delete(self, schedule_id: str, created_by: str, run_count: int) -> None:
        """Soft-delete a schedule, logging a persist failure and resolving a lost race.

        An unexpected persist failure is logged as ``schedule_delete_persist_failed`` before it
        propagates: at that point the job is already unregistered, so the schedule cannot fire
        but is still presented as live, and the log is what tells an operator a retry is needed.
        """
        try:
            marked = await self._repo.mark_deleted(schedule_id, created_by)
        except Exception as exc:
            logger.error("schedule_delete_persist_failed", schedule_id=schedule_id, reason=str(exc))
            raise
        if not marked:
            await self._raise_for_lost_race(schedule_id, created_by)
        logger.info(
            "schedule_marked_deleted",
            schedule_id=schedule_id,
            outcome="marked_deleted",
            run_count=run_count,
        )

    async def delete(self, schedule_id: str, created_by: str) -> None:
        """Delete an owned schedule, preserving its run history when it has any.

        The APScheduler job is unregistered **before** the database is touched, so a successful
        return guarantees the schedule can no longer fire. If the database write then fails, the
        caller receives the error and the schedule is left live-looking but non-firing; a retried
        delete completes the operation, because a missing job is tolerated.

        Which branch runs is decided by run history alone:
        - no runs — the row is removed outright, and the schedule ceases to exist;
        - any run — the row is kept with ``is_deleted`` set, so run history survives.

        The run count is only a fast path. The ``schedule_runs`` foreign key is
        ``ON DELETE RESTRICT``, so if a run is inserted between the count and the delete the
        database blocks it and this falls back to a soft delete, preserving the new run rather
        than failing the request.

        Args:
            schedule_id: Primary key of the schedule to delete.
            created_by: Owner the schedule must belong to.

        Raises:
            ScheduleNotFoundError: The schedule is unknown or owned by someone else.
            ScheduleAlreadyDeletedError: The schedule is already soft-deleted.
            JobRemovalError: The scheduler could not unregister the job; nothing was written.
        """
        row = await self._repo.get(schedule_id, created_by)
        if row is None:
            logger.info("schedule_delete_not_found", schedule_id=schedule_id, reason="not_found_or_not_owned")
            raise ScheduleNotFoundError(schedule_id)
        schedule, _ = row
        if schedule.is_deleted:
            # Rejected before anything was touched, unlike the lost-race conflict raised by
            # _raise_for_lost_race after the job has already been unregistered.
            logger.info("schedule_delete_conflict", schedule_id=schedule_id, reason="already_deleted")
            raise ScheduleAlreadyDeletedError(schedule_id)

        run_count = await self._run_repo.count_for_schedule(schedule_id)
        logger.info("schedule_delete_start", schedule_id=schedule_id, run_count=run_count)

        await self._unregister_job(schedule_id)

        if run_count > 0:
            await self._soft_delete(schedule_id, created_by, run_count)
            return

        try:
            removed = await self._repo.delete_owned(schedule_id, created_by)
        except IntegrityError as exc:
            # A run was created after the count; the FK blocked the delete. Re-count so the
            # soft-delete event reports the real history rather than the stale zero.
            logger.warning(
                "schedule_delete_race_fallback",
                schedule_id=schedule_id,
                reason="run_created_concurrently",
                error=str(exc),
            )
            try:
                run_count = await self._run_repo.count_for_schedule(schedule_id)
            except Exception as count_exc:
                # The job is already unregistered, so this is the state that most needs a log.
                logger.error("schedule_delete_persist_failed", schedule_id=schedule_id, reason=str(count_exc))
                raise
            await self._soft_delete(schedule_id, created_by, run_count)
            return
        except Exception as exc:
            logger.error("schedule_delete_persist_failed", schedule_id=schedule_id, reason=str(exc))
            raise
        if not removed:
            await self._raise_for_lost_race(schedule_id, created_by)
        logger.info("schedule_deleted", schedule_id=schedule_id, outcome="removed", run_count=0)

    async def list(
        self,
        created_by: str,
        paginator: Paginator,
        metadata_filter: dict[str, str] | None = None,
        name_filter: str | None = None,
        ordering: ScheduleOrdering | None = None,
        is_deleted: bool = False,
    ) -> Page[ScheduleListItem]:
        """Return a paginated, owner-scoped list of schedule summaries.

        When ``metadata_filter`` is provided, results are limited to schedules whose
        metadata contains all the given key/value pairs as string values. When
        ``name_filter`` is provided, results are further limited to schedules whose
        display name contains it as a case-insensitive substring.

        ``ordering`` controls sort order (defaults to ``created_at`` descending) and is
        echoed into the ``next``/``previous`` page links so it survives pagination.

        ``is_deleted`` selects live schedules (the default) or soft-deleted ones. A
        non-default value is also echoed into the page links, so traversing a deleted-only
        list cannot silently switch to live schedules on the next page.
        """
        ordering = ordering or ScheduleOrdering()
        paginator.extra_query = ordering.to_query() + ("&is_deleted=true" if is_deleted else "")
        total, rows = await self._repo.list(
            created_by,
            paginator.limit,
            paginator.offset,
            metadata_filter=metadata_filter,
            name_filter=name_filter,
            ordering=ordering,
            is_deleted=is_deleted,
        )

        results = []
        for schedule, next_run_time in rows:
            trigger_type = ScheduleTrigger.model_validate(schedule.trigger).trigger_type
            results.append(
                ScheduleListItem(
                    id=schedule.id,
                    display_name=schedule.display_name,
                    service_id=schedule.service_id,
                    description=schedule.description,
                    next_run_time=next_run_time,
                    trigger_type=trigger_type,
                    trigger=schedule.trigger,
                    is_deleted=schedule.is_deleted,
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
        """Pause an owned schedule's APScheduler job.

        Raises:
            ScheduleNotFoundError: The schedule is unknown or owned by someone else.
            ScheduleAlreadyDeletedError: The schedule is soft-deleted and therefore frozen.
        """
        row = await self._repo.get(schedule_id, created_by)
        if row is None:
            raise ScheduleNotFoundError(schedule_id)
        schedule, _ = row
        if schedule.is_deleted:
            raise ScheduleAlreadyDeletedError(schedule_id)
        try:
            await asyncio.to_thread(self._scheduler.pause_job, schedule_id)
        except JobLookupError as exc:
            raise ScheduleNotFoundError(schedule_id) from exc
        logger.info("schedule_paused", schedule_id=schedule_id)
        row = await self._repo.get(schedule_id, created_by)
        schedule, next_run_time = row  # type: ignore[misc]
        return self._to_response(schedule, next_run_time)

    async def resume(self, schedule_id: str, created_by: str) -> ScheduleResponse:
        """Resume an owned schedule's APScheduler job.

        Raises:
            ScheduleNotFoundError: The schedule is unknown or owned by someone else.
            ScheduleAlreadyDeletedError: The schedule is soft-deleted and therefore frozen —
                a deleted schedule must never be able to start executing again.
        """
        row = await self._repo.get(schedule_id, created_by)
        if row is None:
            raise ScheduleNotFoundError(schedule_id)
        schedule, _ = row
        if schedule.is_deleted:
            raise ScheduleAlreadyDeletedError(schedule_id)
        try:
            await asyncio.to_thread(self._scheduler.resume_job, schedule_id)
        except JobLookupError as exc:
            raise ScheduleNotFoundError(schedule_id) from exc
        logger.info("schedule_resumed", schedule_id=schedule_id)
        row = await self._repo.get(schedule_id, created_by)
        schedule, next_run_time = row  # type: ignore[misc]
        return self._to_response(schedule, next_run_time)

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Column, ColumnElement, CursorResult, delete, func, outerjoin, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from src.lib.db.enums import ScheduleSortField, SortDirection
from src.lib.models.apscheduler_job import apscheduler_jobs
from src.lib.models.schedule import Schedule
from src.lib.schemas.ordering import ScheduleOrdering


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so a filter term matches literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ScheduleRepository:
    """Data access layer for the schedules table with APScheduler job store join."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_next_run_time(epoch: float | None) -> datetime | None:
        """Convert APScheduler epoch float to an aware UTC datetime."""
        if epoch is None:
            return None
        return datetime.fromtimestamp(epoch, tz=UTC)

    async def _get_with_next_run_time(
        self, schedule_id: str, created_by: str
    ) -> tuple[Schedule, datetime | None] | None:
        """Fetch a single owned schedule with its next_run_time from the job store."""
        schedules_table = Schedule.__table__  # type: ignore[attr-defined]
        id_col: Column = schedules_table.c.id  # type: ignore[assignment]
        created_by_col: Column = schedules_table.c.created_by  # type: ignore[assignment]
        stmt = (
            select(Schedule, apscheduler_jobs.c.next_run_time)
            .select_from(outerjoin(schedules_table, apscheduler_jobs, id_col == apscheduler_jobs.c.id))
            .where(id_col == schedule_id, created_by_col == created_by)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        schedule, next_run_epoch = row
        return schedule, self._to_next_run_time(next_run_epoch)

    async def create(self, schedule: Schedule) -> Schedule:
        """Insert a schedule row and commit."""
        self._session.add(schedule)
        await self._session.commit()
        await self._session.refresh(schedule)
        return schedule

    async def delete(self, schedule_id: str) -> None:
        """Delete a schedule row, ignoring ownership (compensating transaction).

        Used only to roll back a freshly created row whose job registration failed. The
        owner-scoped deletion used by the delete endpoint is :meth:`delete_owned`.
        """
        schedule = await self._session.get(Schedule, schedule_id)
        if schedule is not None:
            await self._session.delete(schedule)
            await self._session.commit()

    async def delete_owned(self, schedule_id: str, created_by: str) -> bool:
        """Hard-delete an owned, not-yet-deleted schedule row.

        A single conditional statement, so two concurrent deletes cannot both succeed: the
        ``is_deleted = false`` predicate makes the database the arbiter rather than a
        read-then-write check in the service.

        Args:
            schedule_id: Primary key of the schedule to remove.
            created_by: Owner the schedule must belong to.

        Returns:
            True when the row was removed; False when nothing matched — an unknown id, another
            owner's schedule, or a row that is already soft-deleted.

        Raises:
            sqlalchemy.exc.IntegrityError: When run records still reference the schedule. The
                ``schedule_runs`` foreign key is ``ON DELETE RESTRICT``, so the database refuses
                to destroy run history; callers fall back to :meth:`mark_deleted`. The session is
                rolled back before the exception propagates, because PostgreSQL aborts the whole
                transaction on a failed statement — without it the caller's fallback UPDATE would
                fail with "current transaction is aborted".

                The run-history FK is currently the only constraint referencing ``schedules``, so
                any integrity failure here is that one. Callers log the original error text with
                the fallback, so if another FK is ever added the misattribution is diagnosable
                rather than silent.
        """
        schedules_table = Schedule.__table__  # type: ignore[attr-defined]
        stmt = delete(schedules_table).where(
            schedules_table.c.id == schedule_id,
            schedules_table.c.created_by == created_by,
            schedules_table.c.is_deleted.is_(False),
        )
        try:
            result = cast(CursorResult[Any], await self._session.execute(stmt))
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise
        return bool(result.rowcount == 1)

    async def mark_deleted(self, schedule_id: str, created_by: str) -> bool:
        """Soft-delete an owned schedule by setting ``is_deleted`` to true.

        Conditional on ``is_deleted = false`` for the same concurrency reason as
        :meth:`delete_owned`. ``updated_at`` is refreshed by the column's ``onupdate``, which
        SQLAlchemy applies to Core UPDATE statements.

        Args:
            schedule_id: Primary key of the schedule to mark.
            created_by: Owner the schedule must belong to.

        Returns:
            True when the flag was flipped; False when nothing matched — an unknown id, another
            owner's schedule, or a schedule that was already soft-deleted.
        """
        schedules_table = Schedule.__table__  # type: ignore[attr-defined]
        stmt = (
            update(schedules_table)
            .where(
                schedules_table.c.id == schedule_id,
                schedules_table.c.created_by == created_by,
                schedules_table.c.is_deleted.is_(False),
            )
            .values(is_deleted=True)
        )
        result = cast(CursorResult[Any], await self._session.execute(stmt))
        await self._session.commit()
        return bool(result.rowcount == 1)

    async def update(
        self,
        schedule_id: str,
        created_by: str,
        *,
        display_name: str,
        service_id: str,
        trigger: dict[str, Any],
        kwargs: dict[str, Any],
        timezone: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        updated_at: datetime | None = None,
    ) -> Schedule | None:
        """Update an owned schedule row and commit; returns None if not found/owned.

        Pass `metadata` to replace the stored metadata; omit it to leave it unchanged.
        `description` is always assigned (full replacement, including back to NULL).
        `timezone` is required and always assigned, so no caller can reset it by omission.
        Pass `updated_at` to explicitly restore a prior timestamp (compensating transaction).
        """
        schedule = await self._session.get(Schedule, schedule_id)
        if schedule is None or schedule.created_by != created_by:
            return None
        schedule.display_name = display_name
        schedule.service_id = service_id
        schedule.description = description
        schedule.trigger = trigger
        schedule.kwargs = kwargs
        schedule.timezone = timezone
        if metadata is not None:
            schedule.schedule_metadata = metadata
        if updated_at is not None:
            schedule.updated_at = updated_at
            # Force SQLAlchemy to include updated_at in the SET clause even when
            # the value equals the current DB value (bypasses onupdate=func.now()).
            attributes.flag_modified(schedule, "updated_at")
        self._session.add(schedule)
        await self._session.commit()
        await self._session.refresh(schedule)
        return schedule

    async def get(self, schedule_id: str, created_by: str) -> tuple[Schedule, datetime | None] | None:
        """Return an owned schedule with its next_run_time, or None."""
        return await self._get_with_next_run_time(schedule_id, created_by)

    async def get_by_id(self, schedule_id: str) -> Schedule | None:
        """Return a schedule by primary key regardless of owner, or None.

        Used by the worker at execution time to resolve the owner (``created_by``)
        and ``service_id`` for the on-behalf-of credentials lookup. This is
        authoritative for both new and pre-existing (migrated) schedules, unlike
        APScheduler job kwargs which predate this feature.
        """
        return await self._session.get(Schedule, schedule_id)

    @staticmethod
    def _order_by_clause(
        ordering: ScheduleOrdering,
        *,
        id_col: Column,
        created_at_col: Column,
        display_name_col: Column,
    ) -> list[ColumnElement[Any]]:
        """Build the ORDER BY expression from an ordering value object.

        Inactive/paused schedules (``next_run_time IS NULL``) always sort last via
        ``nulls_last()`` regardless of direction, and the primary key ``id`` is always
        appended as a final tiebreaker for stable pagination.
        """
        direction = ordering.order_dir
        if ordering.order_by is ScheduleSortField.NEXT_RUN_TIME:
            column: ColumnElement[Any] = apscheduler_jobs.c.next_run_time
            primary = (column.asc() if direction is SortDirection.ASC else column.desc()).nulls_last()
        elif ordering.order_by is ScheduleSortField.NAME:
            column = func.lower(display_name_col)
            primary = column.asc() if direction is SortDirection.ASC else column.desc()
        else:
            column = created_at_col
            primary = column.asc() if direction is SortDirection.ASC else column.desc()
        id_order = id_col.asc() if direction is SortDirection.ASC else id_col.desc()
        return [primary, id_order]

    async def list(
        self,
        created_by: str,
        limit: int,
        offset: int,
        *,
        metadata_filter: dict[str, str] | None = None,
        name_filter: str | None = None,
        model_filter: str | None = None,
        ordering: ScheduleOrdering | None = None,
        is_deleted: bool = False,
    ) -> tuple[int, list[tuple[Schedule, datetime | None]]]:
        """Return total count and a page of owned schedules with next_run_times.

        When ``metadata_filter`` is non-empty, only schedules whose ``metadata`` JSONB
        contains all the given key/value pairs as strings are returned (``@>`` containment,
        which matches string values only and composes the pairs with AND).

        When ``name_filter`` is provided, results are further restricted to schedules whose
        ``display_name`` contains it as a case-insensitive substring (``ILIKE``). LIKE
        wildcards (``%``/``_``) in the term are escaped so they match literally. All
        conditions compose with AND.

        ``model_filter`` matches the same way against the generated ``model`` column.
        Schedules whose payload carries no model have ``model IS NULL`` and therefore never
        match, so filtering by model implicitly excludes REST and SSE targets. The predicate
        deliberately reads the column and not ``kwargs -> 'payload' ->> 'model'``: the latter
        detoasts the whole document for every candidate row.

        ``is_deleted`` selects live schedules (the default) or soft-deleted ones; it is applied
        to the count as well as the page, so the reported total always matches the filter.
        """
        schedules_table = Schedule.__table__  # type: ignore[attr-defined]
        id_col: Column = schedules_table.c.id  # type: ignore[assignment]
        created_by_col: Column = schedules_table.c.created_by  # type: ignore[assignment]
        created_at_col: Column = schedules_table.c.created_at  # type: ignore[assignment]
        display_name_col: Column = schedules_table.c.display_name  # type: ignore[assignment]
        metadata_col: Column = schedules_table.c["metadata"]  # type: ignore[assignment]
        model_col: Column = schedules_table.c.model  # type: ignore[assignment]

        is_deleted_col: Column = schedules_table.c.is_deleted  # type: ignore[assignment]

        conditions = [created_by_col == created_by, is_deleted_col.is_(is_deleted)]
        if metadata_filter:
            # JSONB containment (@>) — type-aware, so a string filter matches only
            # string-valued entries, and multiple keys compose with AND.
            conditions.append(metadata_col.contains(dict(metadata_filter)))
        if name_filter:
            conditions.append(display_name_col.ilike(f"%{_escape_like(name_filter)}%", escape="\\"))
        if model_filter:
            conditions.append(model_col.ilike(f"%{_escape_like(model_filter)}%", escape="\\"))

        count_stmt = select(func.count()).select_from(schedules_table).where(*conditions)
        count_result = await self._session.execute(count_stmt)
        total = count_result.scalar_one()

        order_by = self._order_by_clause(
            ordering or ScheduleOrdering(),
            id_col=id_col,
            created_at_col=created_at_col,
            display_name_col=display_name_col,
        )
        page_stmt = (
            select(Schedule, apscheduler_jobs.c.next_run_time)
            .select_from(outerjoin(schedules_table, apscheduler_jobs, id_col == apscheduler_jobs.c.id))
            .where(*conditions)
            .order_by(*order_by)
            .limit(limit)
            .offset(offset)
        )
        rows = await self._session.execute(page_stmt)
        results = [(row[0], self._to_next_run_time(row[1])) for row in rows]
        return total, results

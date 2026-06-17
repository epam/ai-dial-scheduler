from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, func, outerjoin, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from src.lib.models.apscheduler_job import apscheduler_jobs
from src.lib.models.schedule import Schedule


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
        """Delete a schedule row (compensating transaction)."""
        schedule = await self._session.get(Schedule, schedule_id)
        if schedule is not None:
            await self._session.delete(schedule)
            await self._session.commit()

    async def update(
        self,
        schedule_id: str,
        created_by: str,
        *,
        display_name: str,
        trigger: dict[str, Any],
        kwargs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        updated_at: datetime | None = None,
    ) -> Schedule | None:
        """Update an owned schedule row and commit; returns None if not found/owned.

        Pass `metadata` to replace the stored metadata; omit it to leave it unchanged.
        Pass `updated_at` to explicitly restore a prior timestamp (compensating transaction).
        """
        schedule = await self._session.get(Schedule, schedule_id)
        if schedule is None or schedule.created_by != created_by:
            return None
        schedule.display_name = display_name
        schedule.trigger = trigger
        schedule.kwargs = kwargs
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

    async def list(
        self,
        created_by: str,
        limit: int,
        offset: int,
        *,
        metadata_filter: dict[str, str] | None = None,
    ) -> tuple[int, list[tuple[Schedule, datetime | None]]]:
        """Return total count and a page of owned schedules with next_run_times.

        When ``metadata_filter`` is non-empty, only schedules whose ``metadata`` JSONB
        contains all the given key/value pairs as strings are returned (``@>`` containment,
        which matches string values only and composes the pairs with AND).
        """
        schedules_table = Schedule.__table__  # type: ignore[attr-defined]
        id_col: Column = schedules_table.c.id  # type: ignore[assignment]
        created_by_col: Column = schedules_table.c.created_by  # type: ignore[assignment]
        created_at_col: Column = schedules_table.c.created_at  # type: ignore[assignment]
        metadata_col: Column = schedules_table.c["metadata"]  # type: ignore[assignment]

        conditions = [created_by_col == created_by]
        if metadata_filter:
            # JSONB containment (@>) — type-aware, so a string filter matches only
            # string-valued entries, and multiple keys compose with AND.
            conditions.append(metadata_col.contains(dict(metadata_filter)))

        count_stmt = select(func.count()).select_from(schedules_table).where(*conditions)
        count_result = await self._session.execute(count_stmt)
        total = count_result.scalar_one()

        page_stmt = (
            select(Schedule, apscheduler_jobs.c.next_run_time)
            .select_from(outerjoin(schedules_table, apscheduler_jobs, id_col == apscheduler_jobs.c.id))
            .where(*conditions)
            .order_by(created_at_col.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = await self._session.execute(page_stmt)
        results = [(row[0], self._to_next_run_time(row[1])) for row in rows]
        return total, results

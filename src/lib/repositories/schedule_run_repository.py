from datetime import UTC, datetime
from typing import Any
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.enums import ScheduleRunStatus, SortDirection
from src.lib.models.schedule_run import ScheduleRun
from src.lib.schemas.credentials import RunError
from src.lib.schemas.ordering import RunOrdering


class ScheduleRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_in_progress(self, schedule_id: str, schedule_snapshot: dict[str, Any]) -> ScheduleRun:
        row = ScheduleRun(
            status=ScheduleRunStatus.IN_PROGRESS.value,
            end_time=None,
            result=None,
            schedule_id=schedule_id,
            schedule_snapshot=schedule_snapshot,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def mark_success(self, run_id: str, result: dict[str, Any]) -> ScheduleRun | None:
        row = await self._session.get(ScheduleRun, uuid.UUID(run_id))
        if row is None:
            return None
        row.status = ScheduleRunStatus.SUCCESS.value
        row.end_time = datetime.now(UTC)
        row.result = result
        await self._session.commit()
        return row

    async def mark_error(self, run_id: str, error_text: str) -> ScheduleRun | None:
        row = await self._session.get(ScheduleRun, uuid.UUID(run_id))
        if row is None:
            return None
        row.status = ScheduleRunStatus.ERROR.value
        row.end_time = datetime.now(UTC)
        row.result = {"error_text": error_text}
        await self._session.commit()
        return row

    async def mark_credentials_error(self, run_id: str, error: RunError) -> ScheduleRun | None:
        """Mark a run as error from a credentials-retrieval failure.

        Stores the structured :class:`RunError` (``stage="credentials"`` plus the DIAL
        lookup response) so the failure is distinguishable from a target-request error
        (which keeps the plain ``{"error_text": ...}`` shape via :meth:`mark_error`).
        """
        row = await self._session.get(ScheduleRun, uuid.UUID(run_id))
        if row is None:
            return None
        row.status = ScheduleRunStatus.ERROR.value
        row.end_time = datetime.now(UTC)
        row.result = error.model_dump()
        await self._session.commit()
        return row

    async def list_for_schedule(
        self,
        schedule_id: str,
        limit: int,
        offset: int,
        *,
        ordering: RunOrdering | None = None,
    ) -> tuple[int, list[ScheduleRun]]:
        """Return total count and a page of runs for a schedule.

        Runs are ordered by ``created_at`` in the requested direction (defaulting to
        descending) with the primary key ``id`` appended as a final tiebreaker for
        stable pagination.
        """
        ordering = ordering or RunOrdering()
        runs_table = ScheduleRun.__table__  # type: ignore[attr-defined]
        schedule_id_col = runs_table.c.schedule_id
        created_at_col = runs_table.c.created_at
        id_col = runs_table.c.id

        count_stmt = select(func.count()).select_from(runs_table).where(schedule_id_col == schedule_id)
        count_result = await self._session.execute(count_stmt)
        total = count_result.scalar_one()

        ascending = ordering.order_dir is SortDirection.ASC
        created_at_order = created_at_col.asc() if ascending else created_at_col.desc()
        id_order = id_col.asc() if ascending else id_col.desc()
        page_stmt = (
            select(ScheduleRun)
            .where(schedule_id_col == schedule_id)
            .order_by(created_at_order, id_order)
            .limit(limit)
            .offset(offset)
        )
        page_result = await self._session.execute(page_stmt)
        rows = page_result.scalars().all()
        return total, list(rows)

    async def get_for_schedule(self, schedule_id: str, run_id: str) -> ScheduleRun | None:
        """Fetch a run by PK, returning None if it does not belong to the given schedule."""
        row = await self._session.get(ScheduleRun, uuid.UUID(run_id))
        if row is None or row.schedule_id != schedule_id:
            return None
        return row

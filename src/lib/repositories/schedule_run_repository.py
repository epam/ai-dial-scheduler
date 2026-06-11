from datetime import UTC, datetime
from typing import Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.enums import ScheduleRunStatus
from src.lib.models.schedule_run import ScheduleRun


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

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.db import get_db_session
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.repositories.schedule_run_repository import ScheduleRunRepository
from src.lib.services.schedule_run_service import ScheduleRunService


async def get_schedule_run_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AsyncGenerator[ScheduleRunService, None]:
    schedule_repo = ScheduleRepository(session)
    run_repo = ScheduleRunRepository(session)
    yield ScheduleRunService(schedule_repo, run_repo)

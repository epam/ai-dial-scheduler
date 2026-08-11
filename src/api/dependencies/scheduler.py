from collections.abc import AsyncGenerator
from typing import Annotated

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.db import get_db_session
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.repositories.schedule_run_repository import ScheduleRunRepository
from src.lib.services.schedule_service import ScheduleService


def get_scheduler(request: Request) -> AsyncIOScheduler:
    return request.app.state.scheduler  # type: ignore[no-any-return]


async def get_schedule_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    scheduler: Annotated[AsyncIOScheduler, Depends(get_scheduler)],
) -> AsyncGenerator[ScheduleService, None]:
    repository = ScheduleRepository(session)
    run_repository = ScheduleRunRepository(session)
    yield ScheduleService(repository, run_repository, scheduler)

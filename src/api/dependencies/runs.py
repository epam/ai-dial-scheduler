from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.db import get_db_session
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.repositories.schedule_run_repository import ScheduleRunRepository
from src.lib.services.run_launcher import BackgroundRunLauncher
from src.lib.services.schedule_run_service import ScheduleRunService


async def get_schedule_run_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AsyncGenerator[ScheduleRunService, None]:
    schedule_repo = ScheduleRepository(session)
    run_repo = ScheduleRunRepository(session)
    yield ScheduleRunService(schedule_repo, run_repo)


def get_run_launcher(request: Request) -> BackgroundRunLauncher:
    """Return the app-scoped launcher that executes on-demand runs in the background."""
    return request.app.state.run_launcher  # type: ignore[no-any-return]

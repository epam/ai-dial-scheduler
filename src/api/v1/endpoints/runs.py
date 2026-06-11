from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies.pagination import Pagination
from src.api.dependencies.runs import get_schedule_run_service
from src.lib.constants import MOCK_USER_ID
from src.lib.exceptions.errors import ScheduleNotFoundError, ScheduleRunNotFoundError
from src.lib.schemas.pagination import Page
from src.lib.schemas.run import RunListItem, RunResponse
from src.lib.services.schedule_run_service import ScheduleRunService


router = APIRouter(prefix="/schedules/{schedule_id}/runs", tags=["runs"])

ScheduleRunServiceDep = Annotated[ScheduleRunService, Depends(get_schedule_run_service)]
PaginationDep = Annotated[Pagination, Depends()]


@router.get("", response_model=Page[RunListItem])
async def list_runs(
    schedule_id: str,
    service: ScheduleRunServiceDep,
    pagination: PaginationDep,
) -> Page[RunListItem]:
    """Return a paginated run history for an owned schedule."""
    try:
        base_path = f"/schedules/{schedule_id}/runs"
        return await service.list_runs(schedule_id, MOCK_USER_ID, pagination.paginator(base_path))
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    schedule_id: str,
    run_id: uuid.UUID,
    service: ScheduleRunServiceDep,
) -> RunResponse:
    """Return full detail for a specific run under an owned schedule."""
    try:
        return await service.get_run(schedule_id, MOCK_USER_ID, str(run_id))
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc
    except ScheduleRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc

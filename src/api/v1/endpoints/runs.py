from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies.auth import get_user_id
from src.api.dependencies.ordering import RunOrderingParams
from src.api.dependencies.pagination import Pagination
from src.api.dependencies.runs import get_schedule_run_service
from src.lib.exceptions.errors import ScheduleNotFoundError, ScheduleRunNotFoundError
from src.lib.schemas.pagination import Page
from src.lib.schemas.run import RunListItem, RunResponse
from src.lib.services.schedule_run_service import ScheduleRunService


router = APIRouter(prefix="/schedules/{schedule_id}/runs", tags=["runs"])

ScheduleRunServiceDep = Annotated[ScheduleRunService, Depends(get_schedule_run_service)]
PaginationDep = Annotated[Pagination, Depends()]
RunOrderingDep = Annotated[RunOrderingParams, Depends()]


@router.get("", response_model=Page[RunListItem])
async def list_runs(
    schedule_id: str,
    service: ScheduleRunServiceDep,
    pagination: PaginationDep,
    ordering: RunOrderingDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> Page[RunListItem]:
    """Return a paginated run history for an owned schedule.

    Ordering is controlled by `order_by` (`created_at`) and `order_dir` (`asc`, `desc`),
    defaulting to `created_at desc`.
    """
    try:
        base_path = f"/schedules/{schedule_id}/runs"
        return await service.list_runs(schedule_id, user_id, pagination.paginator(base_path), ordering.ordering())
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    schedule_id: str,
    run_id: uuid.UUID,
    service: ScheduleRunServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> RunResponse:
    """Return full detail for a specific run under an owned schedule."""
    try:
        return await service.get_run(schedule_id, user_id, str(run_id))
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc
    except ScheduleRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc

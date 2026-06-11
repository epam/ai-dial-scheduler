from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies.pagination import Pagination
from src.api.dependencies.scheduler import get_schedule_service
from src.lib.constants import MOCK_USER_ID
from src.lib.exceptions import JobRegistrationError, ScheduleNotFoundError
from src.lib.schemas.pagination import Page
from src.lib.schemas.schedule import ScheduleListItem, ScheduleRequest, ScheduleResponse
from src.lib.services.schedule_service import ScheduleService


router = APIRouter(prefix="/schedules", tags=["schedules"])

ScheduleServiceDep = Annotated[ScheduleService, Depends(get_schedule_service)]
PaginationDep = Annotated[Pagination, Depends()]


@router.post("/", status_code=201, response_model=ScheduleResponse)
async def create_schedule(
    request: ScheduleRequest,
    service: ScheduleServiceDep,
) -> ScheduleResponse:
    """Create a new schedule and register it with the job scheduler."""
    try:
        return await service.create(request, MOCK_USER_ID)
    except JobRegistrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str,
    request: ScheduleRequest,
    service: ScheduleServiceDep,
) -> ScheduleResponse:
    """Update an existing schedule and re-register it with the job scheduler."""
    try:
        return await service.update(schedule_id, request, MOCK_USER_ID)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc
    except JobRegistrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/", response_model=Page[ScheduleListItem])
async def list_schedules(
    service: ScheduleServiceDep,
    pagination: PaginationDep,
) -> Page[ScheduleListItem]:
    """Return a paginated, owner-scoped list of schedules."""
    return await service.list(MOCK_USER_ID, pagination.paginator("/schedules"))


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: str,
    service: ScheduleServiceDep,
) -> ScheduleResponse:
    """Return the full detail for an owned schedule."""
    try:
        return await service.get(schedule_id, MOCK_USER_ID)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc

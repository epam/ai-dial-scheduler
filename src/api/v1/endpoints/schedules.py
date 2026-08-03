import json
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException

from src.api.dependencies.auth import get_user_id
from src.api.dependencies.metadata_filter import parse_metadata_filter
from src.api.dependencies.name_filter import parse_name_filter
from src.api.dependencies.ordering import ScheduleOrderingParams
from src.api.dependencies.pagination import Pagination
from src.api.dependencies.scheduler import get_schedule_service
from src.lib.exceptions.errors import JobRegistrationError, ScheduleNotFoundError
from src.lib.schemas.pagination import Page
from src.lib.schemas.schedule import ScheduleListItem, ScheduleRequest, ScheduleResponse
from src.lib.services.schedule_service import ScheduleService


router = APIRouter(prefix="/schedules", tags=["schedules"])

ScheduleServiceDep = Annotated[ScheduleService, Depends(get_schedule_service)]
PaginationDep = Annotated[Pagination, Depends()]
MetadataFilterDep = Annotated[dict[str, str], Depends(parse_metadata_filter)]
NameFilterDep = Annotated[str | None, Depends(parse_name_filter)]
ScheduleOrderingDep = Annotated[ScheduleOrderingParams, Depends()]

_SCHEDULE_EXAMPLES = {
    "chat_completion": {
        "summary": "Chat completion (DIAL model)",
        "value": {
            "display_name": "Daily GPT-4o summary",
            "service_id": "dial-oauth",
            "description": "Posts a GPT-4o summary of the day's highlights every morning.",
            "trigger": {"cron": {"fields": {"hour": 9, "minute": 0}}},
            "properties": {
                "target_type": "chat_completion",
                "url": "http://dial-core.dial.svc.cluster.local/openai/",
                "payload": {
                    "messages": [{"role": "user", "content": "Summarise today's highlights."}],
                    "model": "gpt-4o",
                    "stream": True,
                },
                "api_version": "2025-01-01-preview",
                "create_conversation": False,
            },
        },
    },
    "rest": {
        "summary": "REST POST call",
        "value": {
            "display_name": "Hourly REST ping",
            "service_id": "dial-oauth",
            "trigger": {"cron": {"fields": {"minute": 0}}},
            "properties": {
                "target_type": "rest",
                "method": "POST",
                "url": "https://example.com/api/ping",
                "payload": {"source": "scheduler"},
            },
        },
    },
    "sse": {
        "summary": "SSE streaming call",
        "value": {
            "display_name": "Every-5-min SSE stream",
            "service_id": "dial-oauth",
            "trigger": {"cron": {"fields": {"minute": "*/5"}}},
            "properties": {
                "target_type": "sse",
                "method": "POST",
                "url": "https://example.com/api/stream",
                "payload": {"prompt": "Generate a status update."},
            },
        },
    },
}


def _examples_docstring() -> str:
    parts = ["Examples:"]
    for ex in _SCHEDULE_EXAMPLES.values():
        parts.append(f"\n{ex['summary']}:\n```json\n{json.dumps(ex['value'], indent=2)}\n```")
    return "\n".join(parts)


_EXAMPLES_DOC = _examples_docstring()

ScheduleRequestBody = Annotated[ScheduleRequest, Body(openapi_examples=_SCHEDULE_EXAMPLES)]


@router.post("/", status_code=201, response_model=ScheduleResponse)
async def create_schedule(
    request: ScheduleRequestBody,
    service: ScheduleServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> ScheduleResponse:
    """Create a new schedule and register it with the job scheduler.

    The `properties.target_type` field determines the call style:
    - `chat_completion` — calls a DIAL chat-completions endpoint; set `api_version` if required.
    - `rest` — plain HTTP request; `method` is required.
    - `sse` — Server-Sent Events stream; `method` is required.

    `service_id` identifies the external service whose on-behalf-of credentials are used
    to authenticate each run. Use `service_id: "dial-oauth"` unless a different external
    service is required.
    """
    try:
        return await service.create(request, user_id)
    except JobRegistrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


router.routes[-1].description += "\n\n" + _EXAMPLES_DOC  # type: ignore[union-attr,attr-defined]


@router.put("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str,
    request: ScheduleRequestBody,
    service: ScheduleServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> ScheduleResponse:
    """Replace all fields of an existing schedule and re-register it with the job scheduler.

    `service_id` is required, same as on create; use `"dial-oauth"` unless a different
    external service is required.
    """
    try:
        return await service.update(schedule_id, request, user_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc
    except JobRegistrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


router.routes[-1].description += "\n\n" + _EXAMPLES_DOC  # type: ignore[union-attr,attr-defined]


@router.get("/", response_model=Page[ScheduleListItem])
async def list_schedules(
    service: ScheduleServiceDep,
    pagination: PaginationDep,
    metadata_filter: MetadataFilterDep,
    name_filter: NameFilterDep,
    ordering: ScheduleOrderingDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> Page[ScheduleListItem]:
    """Return a paginated, owner-scoped list of schedules.

    Supports filtering by metadata via repeated `metadata__<key>=<value>` query
    parameters; only schedules whose metadata contains every supplied key with a
    matching string value are returned.

    Supports filtering by name via the `name` query parameter; only schedules whose
    `display_name` contains the given value as a case-insensitive substring are
    returned. Composes with metadata filters and pagination using AND.

    Ordering is controlled by `order_by` (`created_at`, `next_run_time`, `name`) and
    `order_dir` (`asc`, `desc`), defaulting to `created_at desc`. Inactive/paused
    schedules (no next run time) always sort last when ordering by `next_run_time`.
    """
    return await service.list(
        user_id,
        pagination.paginator("/schedules"),
        metadata_filter,
        name_filter=name_filter,
        ordering=ordering.ordering(),
    )


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: str,
    service: ScheduleServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> ScheduleResponse:
    """Return the full detail for an owned schedule."""
    try:
        return await service.get(schedule_id, user_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc


@router.post("/{schedule_id}/pause", response_model=ScheduleResponse)
async def pause_schedule(
    schedule_id: str,
    service: ScheduleServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> ScheduleResponse:
    """Pause an owned schedule so it stops firing."""
    try:
        return await service.pause(schedule_id, user_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc


@router.post("/{schedule_id}/resume", response_model=ScheduleResponse)
async def resume_schedule(
    schedule_id: str,
    service: ScheduleServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> ScheduleResponse:
    """Resume an owned paused schedule so it fires again on its trigger."""
    try:
        return await service.resume(schedule_id, user_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc

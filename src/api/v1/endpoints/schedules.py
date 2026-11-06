import json
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from src.api.dependencies.auth import get_user_id
from src.api.dependencies.metadata_filter import parse_metadata_filter
from src.api.dependencies.name_filter import parse_name_filter
from src.api.dependencies.ordering import ScheduleOrderingParams
from src.api.dependencies.pagination import Pagination
from src.api.dependencies.runs import get_run_launcher, get_schedule_run_service
from src.api.dependencies.scheduler import get_schedule_service
from src.api.dependencies.sign_in import get_sign_in_service
from src.lib.constants import API_KEY_HEADER, CLIENT_CHANNEL_HEADER
from src.lib.exceptions.errors import (
    JobRegistrationError,
    JobRemovalError,
    ScheduleAlreadyDeletedError,
    ScheduleNotFoundError,
    SignInRequiredError,
)
from src.lib.schemas.pagination import Page
from src.lib.schemas.run import RunListItem
from src.lib.schemas.schedule import ScheduleListItem, ScheduleRequest, ScheduleResponse
from src.lib.services.run_launcher import BackgroundRunLauncher
from src.lib.services.schedule_run_service import ScheduleRunService
from src.lib.services.schedule_service import ScheduleService
from src.lib.services.sign_in_service import SignInService


router = APIRouter(prefix="/schedules", tags=["schedules"])

ScheduleServiceDep = Annotated[ScheduleService, Depends(get_schedule_service)]
ScheduleRunServiceDep = Annotated[ScheduleRunService, Depends(get_schedule_run_service)]
RunLauncherDep = Annotated[BackgroundRunLauncher, Depends(get_run_launcher)]
SignInServiceDep = Annotated[SignInService, Depends(get_sign_in_service)]
ChannelIdDep = Annotated[str | None, Header(alias=CLIENT_CHANNEL_HEADER)]
ApiKeyDep = Annotated[str, Header(alias=API_KEY_HEADER)]
PaginationDep = Annotated[Pagination, Depends()]
MetadataFilterDep = Annotated[dict[str, str], Depends(parse_metadata_filter)]
NameFilterDep = Annotated[str | None, Depends(parse_name_filter)]
ScheduleOrderingDep = Annotated[ScheduleOrderingParams, Depends()]


async def _require_sign_in(
    sign_in_service: SignInService,
    user_id: str,
    service_id: str,
    channel_id: str | None,
    api_key: str,
) -> None:
    """Enforce the sign-in precondition on create/update, translating the failure states.

    ``not_signed_in`` and ``unknown`` are deliberately different HTTP statuses (FR-004/FR-008):
    a 403 is something the user can act on, a 502 is an outage and says nothing about their
    sign-in state.
    """
    try:
        await sign_in_service.require(user_id, service_id, api_key, channel_id)
    except SignInRequiredError as exc:
        status_code = 403 if exc.state == "not_signed_in" else 502
        # Reuses SignInStatus's own field set (service_id/state/detail/signed_in) so this
        # rejection body is structurally identical to GET /v1/sign-in/status's response (FR-007).
        raise HTTPException(status_code=status_code, detail=exc.status.model_dump()) from exc


_SCHEDULE_EXAMPLES = {
    "chat_completion": {
        "summary": "Chat completion (DIAL model)",
        "value": {
            "display_name": "Daily GPT-4o summary",
            "service_id": "dial-native",
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
                "create_conversation": True,
                # Streaming example: the ceiling bounds the whole attempt, idle_timeout bounds
                # the gap between events. Agents reading this see both as tool-input guidance.
                "timeout": 1800,
                "idle_timeout": 60,
            },
        },
    },
    "rest": {
        "summary": "REST POST call",
        "value": {
            "display_name": "Hourly REST ping",
            "service_id": "dial-native",
            "trigger": {"cron": {"fields": {"minute": 0}}},
            "properties": {
                "target_type": "rest",
                "method": "POST",
                "url": "https://example.com/api/ping",
                "payload": {"source": "scheduler"},
            },
        },
    },
    "multiple_crons": {
        "summary": "Several cron times (Monday 08:00 and Friday 10:00)",
        "value": {
            "display_name": "Twice-weekly REST ping",
            "service_id": "dial-native",
            "trigger": {
                "crons": [
                    {"fields": {"day_of_week": "mon", "hour": 8, "minute": 0}},
                    {"fields": {"day_of_week": "fri", "hour": 10, "minute": 0}},
                ]
            },
            "properties": {
                "target_type": "rest",
                "method": "POST",
                "url": "https://example.com/api/ping",
                "payload": {"source": "scheduler"},
            },
        },
    },
    "with_timezone": {
        "summary": "Local wall-clock time (weekdays 09:00 in Berlin, all year round)",
        "value": {
            "display_name": "Weekday morning REST ping",
            "service_id": "dial-native",
            "timezone": "Europe/Berlin",
            "trigger": {"cron": {"fields": {"day_of_week": "mon-fri", "hour": 9, "minute": 0}}},
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
            "service_id": "dial-native",
            "trigger": {"cron": {"fields": {"minute": "*/5"}}},
            "properties": {
                "target_type": "sse",
                "method": "POST",
                "url": "https://example.com/api/stream",
                "payload": {"prompt": "Generate a status update."},
                "timeout": 1800,
                "idle_timeout": 60,
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
    sign_in_service: SignInServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
    api_key: ApiKeyDep,
    channel_id: ChannelIdDep = None,
) -> ScheduleResponse:
    """Create a new schedule and register it with the job scheduler.

    The `properties.target_type` field determines the call style:
    - `chat_completion` — calls a DIAL chat-completions endpoint; set `api_version` if required.
    - `rest` — plain HTTP request; `method` is required.
    - `sse` — Server-Sent Events stream; `method` is required.

    For `chat_completion` schedules, set `properties.create_conversation: true` unless the caller
    asked for runs not to be saved: each successful run is then stored as a DIAL conversation
    titled after the schedule's `display_name`, so the answer is readable in DIAL Chat instead of
    only through the runs API. It is rejected for `rest`/`sse`/`responses`.

    `service_id` identifies the external service whose on-behalf-of credentials are used
    to authenticate each run. Use `service_id: "dial-native"` unless a different external
    service is required. Creating a schedule for a service the caller is not signed in to is
    rejected with `403` and nothing is created. Use `GET /v1/sign-in/status` to check first;
    on a `403`, relay the returned message to the user — only they can complete the sign-in.
    """
    await _require_sign_in(sign_in_service, user_id, request.service_id, channel_id, api_key)
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
    sign_in_service: SignInServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
    api_key: ApiKeyDep,
    channel_id: ChannelIdDep = None,
) -> ScheduleResponse:
    """Replace all fields of an existing schedule and re-register it with the job scheduler.

    `service_id` is required, same as on create; use `"dial-native"` unless a different
    external service is required. Updating a schedule to target a service the caller is not
    signed in to is rejected with `403` and the existing schedule is left unchanged.
    """
    await _require_sign_in(sign_in_service, user_id, request.service_id, channel_id, api_key)
    try:
        return await service.update(schedule_id, request, user_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc
    except ScheduleAlreadyDeletedError as exc:
        raise HTTPException(status_code=409, detail=f"Schedule already deleted: {schedule_id}") from exc
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
    is_deleted: Annotated[bool, Query()] = False,
    model: Annotated[
        str | None,
        Query(
            description=(
                "Filter by the schedule's model, as a case-insensitive substring of "
                "`properties.payload.model` — for example `gpt-4` matches `gpt-4o` and "
                "`gpt-4.1-mini`. Schedules whose payload carries no model (REST and SSE "
                "targets) never match, so this also narrows the list to model-backed targets."
            ),
        ),
    ] = None,
) -> Page[ScheduleListItem]:
    """Return a paginated, owner-scoped list of schedules.

    Supports filtering by metadata via repeated `metadata__<key>=<value>` query
    parameters; only schedules whose metadata contains every supplied key with a
    matching string value are returned.

    Supports filtering by name via the `name` query parameter; only schedules whose
    `display_name` contains the given value as a case-insensitive substring are
    returned. Composes with metadata filters and pagination using AND.

    Supports filtering by model via the `model` query parameter, matched the same way
    against each schedule's `properties.payload.model`. Each row also carries a `model`
    field, which is omitted for schedules whose payload has none — REST and SSE targets,
    most commonly — so it must not be relied on as always present.

    Ordering is controlled by `order_by` (`created_at`, `next_run_time`, `name`) and
    `order_dir` (`asc`, `desc`), defaulting to `created_at desc`. Inactive/paused
    schedules (no next run time) always sort last when ordering by `next_run_time`.

    Filtering by deletion state uses the `is_deleted` query parameter, which defaults to
    `false` so the list shows only live schedules. Set it to `true` to list soft-deleted
    schedules instead; there is no value that returns both. Schedules that were deleted
    outright (they had no run history) are absent under either value. `count` reflects the
    filter, and the `next`/`previous` links preserve a non-default value.
    """
    return await service.list(
        user_id,
        pagination.paginator("/schedules"),
        metadata_filter,
        name_filter=name_filter,
        ordering=ordering.ordering(),
        is_deleted=is_deleted,
        model_filter=(model or "").strip() or None,
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
    except ScheduleAlreadyDeletedError as exc:
        raise HTTPException(status_code=409, detail=f"Schedule already deleted: {schedule_id}") from exc


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
    except ScheduleAlreadyDeletedError as exc:
        raise HTTPException(status_code=409, detail=f"Schedule already deleted: {schedule_id}") from exc


@router.post("/{schedule_id}/run", response_model=RunListItem, status_code=202, operation_id="run_schedule_now")
async def run_schedule_now(
    schedule_id: str,
    run_service: ScheduleRunServiceDep,
    launcher: RunLauncherDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> RunListItem:
    """Run an owned schedule once, right now, without waiting for its trigger.

    Returns `202` immediately with a run whose `status` is `in_progress`; execution continues
    in the background. Poll `GET /schedules/{schedule_id}/runs/{run_id}` for the outcome — the
    run's `status`, `end_time` and `result` are filled in when it finishes, and a DIAL
    conversation is created for chat completion schedules that opted into
    `properties.create_conversation`.

    The run uses the schedule's stored definition exactly as a scheduled fire would; there is
    no request body and nothing can be overridden. The schedule's own timetable is untouched,
    so `next_run_time` does not move and the next scheduled fire still happens as planned.
    Triggering a schedule that already has a run in progress is allowed.

    A failure to obtain the owner's credentials is recorded on the run itself (a
    `credentials`-stage result), not reported here — this endpoint only reports whether the
    run was accepted.
    """
    try:
        run = await run_service.create_on_demand_run(schedule_id, user_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc
    except ScheduleAlreadyDeletedError as exc:
        raise HTTPException(status_code=409, detail=f"Schedule already deleted: {schedule_id}") from exc

    launcher.launch(str(run.id), schedule_id, dict(run.schedule_snapshot))
    return RunListItem(
        id=run.id,
        status=run.status,
        start_time=run.created_at,
        end_time=run.end_time,
        conversation_id=run.conversation_id,
    )


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: str,
    service: ScheduleServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> None:
    """Delete an owned schedule, keeping its run history if it has any.

    The schedule always stops firing. What happens to the record depends on run history:
    - **no runs** — the schedule is removed entirely and afterwards returns 404.
    - **any run** — the schedule is kept and marked `is_deleted`; every run record survives
      and stays readable via `GET /schedules/{schedule_id}/runs`.

    There is no way to restore a deleted or soft-deleted schedule. Deleting an already
    soft-deleted schedule returns 409, and so do update, pause, and resume.

    A 204 guarantees the schedule can no longer fire. A 502 means the scheduler could not
    unregister the job and nothing was changed, so the request is safe to retry.
    """
    try:
        await service.delete(schedule_id, user_id)
    except ScheduleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc
    except ScheduleAlreadyDeletedError as exc:
        raise HTTPException(status_code=409, detail=f"Schedule already deleted: {schedule_id}") from exc
    except JobRemovalError as exc:
        # The underlying text comes from the APScheduler job store and can carry SQL and
        # connection diagnostics, so it stays in the log (emitted by the service) and never
        # reaches the client.
        raise HTTPException(
            status_code=502,
            detail="Failed to unregister the schedule's job; nothing was changed. Retry the request.",
        ) from exc

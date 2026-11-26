import asyncio
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from pydantic import ValidationError
from structlog.contextvars import bind_contextvars, clear_contextvars

from src.lib.clients.dial_client import DialClient
from src.lib.constants import (
    CONVERSATION_LOCATION_TIMEOUT_SECONDS,
    EXTERNAL_SERVICE_URL_TEMPLATE,
    HEARTBEAT_JOB_NAME,
    MAX_CONVERSATION_ID_HEADER_BYTES,
)
from src.lib.db.engine import Database
from src.lib.db.enums import TargetType
from src.lib.exceptions.errors import ConversationCreationError, OboCredentialsError, ScheduleRunError
from src.lib.models.schedule import Schedule as _Schedule  # noqa: F401 — registers schedules table for FK resolution
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.repositories.schedule_run_repository import ScheduleRunRepository
from src.lib.schemas.credentials import OboCredentials, OboCredentialsRequest, RunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.settings import get_settings
from src.lib.utils.conversation_header import encode_header_value
from src.lib.utils.logging import logger
from src.worker.clients import HttpClientProvider
from src.worker.conversation import (
    conversation_id_for,
    create_run_conversation,
    deployment_kind,
    resolve_conversation_location,
)
from src.worker.dispatcher import resolve_executor
from src.worker.health import touch_heartbeat
from src.worker.retry import execute_with_policy


_tracer = trace.get_tracer(__name__)


async def _persist_success(run_id: str, schedule_id: str, result: dict[str, Any]) -> None:
    try:
        async with Database().sessionmaker() as session:
            row = await ScheduleRunRepository(session).mark_success(run_id, result)
    except Exception as exc:
        logger.error("run_persist_failed", run_id=run_id, schedule_id=schedule_id, error=str(exc))
        return
    if row is None:
        logger.warning("run_record_missing", run_id=run_id, schedule_id=schedule_id)
    else:
        logger.info("run_recorded_success", run_id=run_id, schedule_id=schedule_id)


async def _persist_error(run_id: str, schedule_id: str, error_text: str) -> None:
    try:
        async with Database().sessionmaker() as session:
            row = await ScheduleRunRepository(session).mark_error(run_id, error_text)
    except Exception as exc:
        logger.error("run_persist_failed", run_id=run_id, schedule_id=schedule_id, error=str(exc))
        return
    if row is None:
        logger.warning("run_record_missing", run_id=run_id, schedule_id=schedule_id)
    else:
        logger.info("run_recorded_error", run_id=run_id, schedule_id=schedule_id)


async def _persist_credentials_error(run_id: str, schedule_id: str, error: RunError) -> None:
    try:
        async with Database().sessionmaker() as session:
            row = await ScheduleRunRepository(session).mark_credentials_error(run_id, error)
    except Exception as exc:
        logger.error("run_persist_failed", run_id=run_id, schedule_id=schedule_id, error=str(exc))
        return
    if row is None:
        logger.warning("run_record_missing", run_id=run_id, schedule_id=schedule_id)
    else:
        logger.info("run_recorded_error", run_id=run_id, schedule_id=schedule_id)


async def _maybe_create_conversation(
    ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties, result: dict[str, Any]
) -> None:
    """Best-effort DIAL conversation creation after a successful chat completion run.

    Only runs for chat completion schedules that opted in via ``create_conversation``, and only
    when the conversation location was resolved before the run -- otherwise the run call carried no
    ``X-CONVERSATION-ID`` and a conversation saved now would sit in a different session, so it is
    skipped. Any failure is logged and swallowed so it never changes the already-successful run
    outcome. No prompt/response content or credentials are logged.
    """
    if properties.target_type != TargetType.CHAT_COMPLETION or not properties.create_conversation:
        return
    if ctx.conversation_location is None:
        logger.info(
            "conversation_create_skipped",
            run_id=ctx.run_id,
            schedule_id=ctx.schedule_id,
            reason="conversation_id_unresolved",
        )
        return
    try:
        conversation_id = await create_run_conversation(ctx, credentials, properties, result)
        async with Database().sessionmaker() as session:
            row = await ScheduleRunRepository(session).set_conversation_id(ctx.run_id, conversation_id)
        if row is None:
            logger.warning(
                "conversation_id_persist_failed",
                run_id=ctx.run_id,
                schedule_id=ctx.schedule_id,
                reason="run_record_missing",
            )
        else:
            logger.info(
                "conversation_id_persisted",
                run_id=ctx.run_id,
                schedule_id=ctx.schedule_id,
                conversation_id=conversation_id,
            )
    except Exception as exc:
        logger.warning(
            "conversation_create_failed",
            run_id=ctx.run_id,
            schedule_id=ctx.schedule_id,
            reason=str(exc),
        )


async def _resolve_conversation_location(
    ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties
) -> None:
    """Resolve the run's conversation location once, before any attempt, for opted-in chat completions.

    Sets ``ctx.conversation_location`` so the conversation is later saved at that location, and
    ``ctx.conversation_id`` so every attempt sends the same ``X-CONVERSATION-ID``. Best-effort, like
    ``_maybe_create_conversation``: a failure (or exceeding ``CONVERSATION_LOCATION_TIMEOUT_SECONDS``,
    since this runs outside the run's own timeout) is logged once and leaves both unset, so the
    request goes out without the header and no conversation is saved -- a run must never fail
    because of conversation work. An id too long for a header keeps the location (the conversation
    is still saved) and only omits the header.
    """
    if properties.target_type != TargetType.CHAT_COMPLETION or not properties.create_conversation:
        return
    try:
        async with asyncio.timeout(CONVERSATION_LOCATION_TIMEOUT_SECONDS):
            location = await resolve_conversation_location(ctx, credentials, properties)
    except ConversationCreationError as exc:
        logger.warning(
            "conversation_id_resolve_failed", run_id=ctx.run_id, schedule_id=ctx.schedule_id, reason=str(exc)
        )
        return
    except TimeoutError:
        logger.warning(
            "conversation_id_resolve_failed",
            run_id=ctx.run_id,
            schedule_id=ctx.schedule_id,
            reason=f"timed out after {CONVERSATION_LOCATION_TIMEOUT_SECONDS}s",
        )
        return
    except Exception as exc:
        # Not an expected remote/input fault: keep the traceback so the bug can be diagnosed.
        logger.warning(
            "conversation_id_resolve_failed",
            run_id=ctx.run_id,
            schedule_id=ctx.schedule_id,
            reason=str(exc),
            exc_info=True,
        )
        return
    ctx.conversation_location = location
    conversation_id = conversation_id_for(location)
    # Every later log entry of the run carries the raw id; callers clear the context after the run.
    bind_contextvars(conversation_id=conversation_id)
    logger.info(
        "conversation_id_resolved",
        run_id=ctx.run_id,
        schedule_id=ctx.schedule_id,
        deployment_kind=deployment_kind(location.deployment_id),
    )
    # display_name has no length limit; an oversized header would make the target reject the run.
    encoded_length = len(encode_header_value(conversation_id))
    if encoded_length > MAX_CONVERSATION_ID_HEADER_BYTES:
        logger.warning(
            "conversation_id_header_omitted",
            run_id=ctx.run_id,
            schedule_id=ctx.schedule_id,
            reason=f"encoded id exceeds {MAX_CONVERSATION_ID_HEADER_BYTES} bytes",
            encoded_length=encoded_length,
        )
        return
    ctx.conversation_id = conversation_id


async def _resolve_credentials(ctx: RunContext) -> OboCredentials:
    """Fetch on-behalf-of credentials for a schedule's owner and external service.

    Loads the schedule (owner + service_id) from the database, then calls DIAL for OBO
    credentials. On any failure — missing schedule or a failed OBO lookup — persists a
    credentials-stage run error (with the DIAL response) and raises ScheduleRunError so
    the target request is never sent and the retry policy is not consumed. The returned
    ``header_value`` is secret and is never logged.
    """
    settings = get_settings()
    run_id, schedule_id = ctx.run_id, ctx.schedule_id
    try:
        async with Database().sessionmaker() as session:
            schedule = await ScheduleRepository(session).get_by_id(schedule_id)
    except Exception as exc:
        error = RunError(stage="credentials", error_text=f"schedule lookup failed: {exc}")
        logger.error(
            "credentials_fetch_failed", run_id=run_id, schedule_id=schedule_id, reason="schedule_lookup_failed"
        )
        await _persist_credentials_error(run_id, schedule_id, error)
        raise ScheduleRunError(error.error_text, schedule_id=schedule_id, run_id=run_id) from exc

    if schedule is None:
        error = RunError(stage="credentials", error_text=f"schedule not found: {schedule_id}")
        logger.warning("credentials_fetch_failed", run_id=run_id, schedule_id=schedule_id, reason="schedule_not_found")
        await _persist_credentials_error(run_id, schedule_id, error)
        raise ScheduleRunError(error.error_text, schedule_id=schedule_id, run_id=run_id)

    logger.info(
        "credentials_fetch_start",
        run_id=run_id,
        schedule_id=schedule_id,
        service_id=schedule.service_id,
        owner_user_id=schedule.created_by,
    )
    request = OboCredentialsRequest(
        url=EXTERNAL_SERVICE_URL_TEMPLATE.format(app_id=settings.scheduler_app_id, service_id=schedule.service_id),
        owner_user_id=schedule.created_by,
    )
    client = DialClient(ctx.http_client or HttpClientProvider().client, settings.dial_url)
    try:
        credentials = await client.get_obo_credentials(settings.dial_api_key.get_secret_value(), request)
    except OboCredentialsError as exc:
        error = RunError(
            stage="credentials",
            error_text=str(exc),
            credentials_response={"status": exc.status_code, "body": exc.response_body},
        )
        logger.warning(
            "credentials_fetch_failed",
            run_id=run_id,
            schedule_id=schedule_id,
            service_id=schedule.service_id,
            status_code=exc.status_code,
        )
        await _persist_credentials_error(run_id, schedule_id, error)
        raise ScheduleRunError(str(exc), schedule_id=schedule_id, run_id=run_id) from exc

    logger.info(
        "credentials_fetch_succeeded",
        run_id=run_id,
        schedule_id=schedule_id,
        service_id=schedule.service_id,
    )
    # Recorded from the row already loaded here, so conversation work never reads the schedule again.
    ctx.schedule_display_name = schedule.display_name
    return credentials


async def execute_scheduled_job(schedule_id: str, **kwargs: Any) -> None:
    with _tracer.start_as_current_span(f"job:schedule:{schedule_id}"):
        await _execute_scheduled_job_inner(schedule_id, **kwargs)


async def _should_skip_run(schedule_id: str) -> bool:
    """Return True when the schedule no longer exists or has been soft-deleted.

    The API and the worker are separate processes sharing one job store, so the worker can
    already have fetched a due job by the time the API unregisters it. Without this check a
    deleted schedule could still produce a run record. An unexpected lookup failure returns
    False so a legitimate run is never silently dropped — the foreign key and the credentials
    lookup remain as backstops.
    """
    try:
        async with Database().sessionmaker() as session:
            schedule = await ScheduleRepository(session).get_by_id(schedule_id)
    except Exception as exc:
        logger.warning("schedule_delete_check_failed", schedule_id=schedule_id, reason=str(exc))
        return False

    if schedule is None:
        logger.info("run_skipped_schedule_deleted", schedule_id=schedule_id, reason="schedule_missing")
        return True
    if schedule.is_deleted:
        logger.info("run_skipped_schedule_deleted", schedule_id=schedule_id, reason="schedule_deleted")
        return True
    return False


async def execute_run(ctx: RunContext, snapshot: dict[str, Any]) -> None:
    """Execute one already-recorded run of a schedule and persist its outcome.

    Shared by the worker's scheduled dispatch and the API's on-demand run endpoint, so both
    produce identical run records. The caller owns the ``in_progress`` row and decides which
    HTTP client and default timeout the run uses (via ``ctx``); this function resolves the
    owner's on-behalf-of credentials, dispatches the target under the schedule's timeout and
    retry policy, writes the terminal status/result, and — for opted-in chat completion
    schedules — creates the DIAL conversation.

    ``snapshot`` is the stored schedule properties; nothing is re-read from the request, so a
    run always reflects the saved definition. Raises :class:`ScheduleRunError` after the
    failure has already been persisted, for the caller to log or propagate.
    """
    run_id, schedule_id = ctx.run_id, ctx.schedule_id
    try:
        try:
            properties = ScheduleProperties(**snapshot)
        except ValidationError as exc:
            err = ScheduleRunError(str(exc), schedule_id=schedule_id, run_id=run_id)
            logger.warning(
                "run_failed",
                run_id=run_id,
                schedule_id=schedule_id,
                reason="invalid_schedule_properties",
                error=str(err),
            )
            await _persist_error(run_id, schedule_id, str(err))
            raise err from exc

        credentials = await _resolve_credentials(ctx)
        await _resolve_conversation_location(ctx, credentials, properties)

        executor = resolve_executor(properties.target_type)
        outcome = await execute_with_policy(ctx, credentials, properties, executor)

        if outcome.succeeded:
            result = outcome.result or {}
            await _persist_success(run_id, schedule_id, result)
            logger.info("run_complete", run_id=run_id, schedule_id=schedule_id, attempts=outcome.attempt_count)
            await _maybe_create_conversation(ctx, credentials, properties, result)
        else:
            error = outcome.error or ScheduleRunError("run failed", schedule_id=schedule_id, run_id=run_id)
            logger.warning(
                "run_failed",
                run_id=run_id,
                schedule_id=schedule_id,
                error=str(error),
                status_code=error.status_code,
                attempts=outcome.attempt_count,
            )
            await _persist_error(run_id, schedule_id, str(error))
            raise error
    except ScheduleRunError:
        raise
    except Exception as exc:
        # Only unexpected failures reach here: executors and the retry orchestrator raise
        # ScheduleRunError for every remote/user fault. This is the scheduler's own fault, so it
        # is the one run failure logged at error level, with a traceback.
        err = ScheduleRunError(str(exc), schedule_id=schedule_id, run_id=run_id)
        logger.error(
            "run_failed",
            run_id=run_id,
            schedule_id=schedule_id,
            reason="unexpected_error",
            error=str(err),
            exc_info=True,
        )
        await _persist_error(run_id, schedule_id, str(err))
        raise err from exc


async def _execute_scheduled_job_inner(schedule_id: str, **kwargs: Any) -> None:
    clear_contextvars()
    bind_contextvars(schedule_id=schedule_id)
    logger.info("run_start", schedule_id=schedule_id)

    # Must precede run-record creation: a deleted schedule must produce no run record at all.
    if await _should_skip_run(schedule_id):
        clear_contextvars()
        return

    try:
        async with Database().sessionmaker() as session:
            row = await ScheduleRunRepository(session).create_in_progress(
                schedule_id=schedule_id, schedule_snapshot=dict(kwargs)
            )
        run_id = str(row.id)
        bind_contextvars(run_id=run_id)
        logger.info("run_created", run_id=run_id, schedule_id=schedule_id)
    except Exception as exc:
        logger.error("run_create_failed", schedule_id=schedule_id, error=str(exc))
        clear_contextvars()
        return

    try:
        await execute_run(RunContext(run_id=run_id, schedule_id=schedule_id), dict(kwargs))
    finally:
        clear_contextvars()


async def heartbeat() -> None:
    with _tracer.start_as_current_span("job:heartbeat"):
        clear_contextvars()
        logger.info("heartbeat", job_name=HEARTBEAT_JOB_NAME, executed_at=datetime.now(UTC).isoformat())
        await touch_heartbeat()

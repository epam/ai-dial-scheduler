from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from pydantic import ValidationError
from structlog.contextvars import bind_contextvars, clear_contextvars

from src.lib.constants import HEARTBEAT_JOB_NAME
from src.lib.db.engine import Database
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.models.schedule import Schedule as _Schedule  # noqa: F401 — registers schedules table for FK resolution
from src.lib.repositories.schedule_run_repository import ScheduleRunRepository
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.utils.logging import logger
from src.worker.dispatcher import resolve_executor
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


async def execute_scheduled_job(schedule_id: str, **kwargs: Any) -> None:
    with _tracer.start_as_current_span(f"job:schedule:{schedule_id}"):
        await _execute_scheduled_job_inner(schedule_id, **kwargs)


async def _execute_scheduled_job_inner(schedule_id: str, **kwargs: Any) -> None:
    clear_contextvars()
    bind_contextvars(schedule_id=schedule_id)
    logger.info("run_start", schedule_id=schedule_id)

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
        try:
            properties = ScheduleProperties(**kwargs)
        except ValidationError as exc:
            err = ScheduleRunError(str(exc), schedule_id=schedule_id, run_id=run_id)
            logger.error("run_failed", run_id=run_id, schedule_id=schedule_id, error=str(err))
            await _persist_error(run_id, schedule_id, str(err))
            raise err from exc

        executor = resolve_executor(properties.target_type)
        ctx = RunContext(run_id=run_id, schedule_id=schedule_id)
        outcome = await execute_with_policy(ctx, properties, executor)

        if outcome.succeeded:
            await _persist_success(run_id, schedule_id, outcome.result or {})
            logger.info("run_complete", run_id=run_id, schedule_id=schedule_id, attempts=outcome.attempt_count)
        else:
            error = outcome.error or ScheduleRunError("run failed", schedule_id=schedule_id, run_id=run_id)
            logger.error(
                "run_failed",
                run_id=run_id,
                schedule_id=schedule_id,
                error=str(error),
                attempts=outcome.attempt_count,
            )
            await _persist_error(run_id, schedule_id, str(error))
            raise error
    except ScheduleRunError:
        raise
    except Exception as exc:
        err = ScheduleRunError(str(exc), schedule_id=schedule_id, run_id=run_id)
        logger.error("run_failed", run_id=run_id, schedule_id=schedule_id, error=str(err))
        await _persist_error(run_id, schedule_id, str(err))
        raise err from exc
    finally:
        clear_contextvars()


async def heartbeat() -> None:
    with _tracer.start_as_current_span("job:heartbeat"):
        clear_contextvars()
        logger.info("heartbeat", job_name=HEARTBEAT_JOB_NAME, executed_at=datetime.now(UTC).isoformat())

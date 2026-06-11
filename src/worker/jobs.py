from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from structlog.contextvars import bind_contextvars, clear_contextvars

from src.lib.constants import HEARTBEAT_JOB_NAME
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.utils.logging import logger
from src.worker.dispatcher import resolve_executor


async def execute_scheduled_job(schedule_id: str, **kwargs: Any) -> None:
    run_id = str(uuid4())
    ctx = RunContext(run_id=run_id, schedule_id=schedule_id)
    clear_contextvars()
    bind_contextvars(**ctx.model_dump())
    logger.info("run_start", schedule_id=schedule_id, run_id=run_id)

    try:
        properties = ScheduleProperties(**kwargs)
        executor = resolve_executor(properties.target_type)
        await executor.execute(ctx, properties)
    except ScheduleRunError:
        raise
    except ValidationError as exc:
        raise ScheduleRunError(str(exc), schedule_id=schedule_id, run_id=run_id) from exc
    except Exception as exc:
        raise ScheduleRunError(str(exc), schedule_id=schedule_id, run_id=run_id) from exc
    finally:
        clear_contextvars()


async def heartbeat() -> None:
    clear_contextvars()
    logger.info("heartbeat", job_name=HEARTBEAT_JOB_NAME, executed_at=datetime.now(UTC).isoformat())

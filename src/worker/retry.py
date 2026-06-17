import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.lib.exceptions.errors import ScheduleRunError
from src.lib.schemas.run import RunContext
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.settings import get_settings
from src.lib.utils.logging import logger
from src.worker.executors.base import Executor


class AttemptEvent(StrEnum):
    START = "attempt_start"
    TIMED_OUT = "attempt_timed_out"
    SUCCEEDED = "attempt_succeeded"
    FAILED = "attempt_failed"
    FAILED_NON_RETRYABLE = "attempt_failed_non_retryable"
    RETRY_SCHEDULED = "retry_scheduled"
    LAST_RETRY_FAILED = "last_retry_failed"


class FailureReason(StrEnum):
    NO_RETRY_POLICY = "no_retry_policy"
    NO_STATUS_CODE = "no_status_code"
    STATUS_NOT_IN_RETRYABLE = "status_not_in_retryable"


@dataclass
class RunOutcome:
    """Result of running a schedule's target under its timeout and retry policy.

    Attributes:
        succeeded: Whether a single attempt ultimately succeeded.
        result: The successful executor result, or ``None`` on final failure.
        error: The terminal error, or ``None`` on success.
        attempt_count: Total attempts made (initial + retries). For summary logging only.
    """

    succeeded: bool
    result: dict[str, Any] | None
    error: ScheduleRunError | None
    attempt_count: int


async def execute_with_policy(ctx: RunContext, properties: ScheduleProperties, executor: Executor) -> RunOutcome:
    """Run the executor honoring the schedule's per-attempt timeout and retry policy.

    A single effective timeout (the schedule's ``timeout`` or the default worker HTTP
    timeout) is computed once, stored on ``ctx.effective_timeout`` so executors apply the
    same value to their underlying HTTP/SDK call, and enforced as an absolute per-attempt
    cap via ``asyncio.wait_for``. A failed attempt is retried only when its HTTP status
    code is in ``retry.retryable_errors`` and retries remain; the orchestrator waits the
    corresponding ``interval_sequence`` entry before retrying. Timeouts and transport
    errors carry no status code and are therefore terminal.

    Each attempt, retry, and terminal outcome is recorded via structured logs; per-attempt
    detail is intentionally not persisted to the database.

    Returns:
        A :class:`RunOutcome` describing the final result and the number of attempts made.
    """
    effective_timeout = (
        properties.timeout if properties.timeout is not None else get_settings().worker_http.timeout_seconds
    )
    ctx.effective_timeout = effective_timeout

    retry = properties.retry
    backoff = retry.interval_sequence if retry is not None else []
    retryable = set(retry.retryable_errors) if retry is not None else set()
    retryable_log = sorted(retryable) if retryable else None
    max_attempts = 1 + len(backoff)

    for attempt in range(1, max_attempts + 1):
        is_retry = attempt > 1
        logger.info(
            AttemptEvent.START,
            schedule_id=ctx.schedule_id,
            run_id=ctx.run_id,
            attempt=attempt,
            max_attempts=max_attempts,
            is_retry=is_retry,
            timeout=effective_timeout,
            retryable_errors=retryable_log,
        )

        try:
            result = await asyncio.wait_for(executor.execute(ctx, properties), timeout=effective_timeout)
        except asyncio.TimeoutError:
            error = ScheduleRunError(
                f"execution timed out after {effective_timeout}s",
                schedule_id=ctx.schedule_id,
                run_id=ctx.run_id,
            )
            logger.error(
                AttemptEvent.TIMED_OUT,
                schedule_id=ctx.schedule_id,
                run_id=ctx.run_id,
                attempt=attempt,
                is_retry=is_retry,
                timeout=effective_timeout,
            )
            return RunOutcome(succeeded=False, result=None, error=error, attempt_count=attempt)
        except ScheduleRunError as exc:
            status_code = exc.status_code
            retries_remain = attempt < max_attempts
            should_retry = retries_remain and status_code is not None and status_code in retryable

            if should_retry:
                wait_seconds = backoff[attempt - 1]
                logger.warning(
                    AttemptEvent.RETRY_SCHEDULED,
                    schedule_id=ctx.schedule_id,
                    run_id=ctx.run_id,
                    attempt=attempt,
                    next_attempt=attempt + 1,
                    backoff_seconds=wait_seconds,
                    status_code=status_code,
                )
                await asyncio.sleep(wait_seconds)
                continue

            if retry is not None and is_retry and attempt == max_attempts:
                logger.error(
                    AttemptEvent.LAST_RETRY_FAILED,
                    schedule_id=ctx.schedule_id,
                    run_id=ctx.run_id,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    status_code=status_code,
                    error=str(exc),
                )
            else:
                if retry is None:
                    reason = FailureReason.NO_RETRY_POLICY
                elif status_code is None:
                    reason = FailureReason.NO_STATUS_CODE
                else:
                    reason = FailureReason.STATUS_NOT_IN_RETRYABLE
                event = AttemptEvent.FAILED_NON_RETRYABLE if retry is not None else AttemptEvent.FAILED
                logger.error(
                    event,
                    schedule_id=ctx.schedule_id,
                    run_id=ctx.run_id,
                    attempt=attempt,
                    status_code=status_code,
                    reason=reason,
                    error=str(exc),
                )
            return RunOutcome(succeeded=False, result=None, error=exc, attempt_count=attempt)
        else:
            logger.info(
                AttemptEvent.SUCCEEDED,
                schedule_id=ctx.schedule_id,
                run_id=ctx.run_id,
                attempt=attempt,
                is_retry=is_retry,
            )
            return RunOutcome(succeeded=True, result=result, error=None, attempt_count=attempt)

    # Unreachable: the final attempt always returns. Guard for type-checkers.
    raise RuntimeError("execute_with_policy loop exited without returning")  # pragma: no cover

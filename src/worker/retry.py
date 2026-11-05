import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.lib.exceptions.errors import ScheduleRunError, StreamIdleTimeoutError
from src.lib.schemas.credentials import OboCredentials
from src.lib.schemas.run import RunContext, RunTimeouts
from src.lib.schemas.schedule import ScheduleProperties
from src.lib.settings import get_settings
from src.lib.utils.logging import logger
from src.worker.executors.base import Executor


class AttemptEvent(StrEnum):
    START = "attempt_start"
    TIMED_OUT = "attempt_timed_out"
    IDLE_TIMED_OUT = "attempt_idle_timed_out"
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


async def execute_with_policy(
    ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties, executor: Executor
) -> RunOutcome:
    """Run the executor honoring the schedule's per-attempt bounds and retry policy.

    Two independent bounds are resolved once and stored on the context, so executors apply the
    same values as the orchestrator and never re-read ``properties``:

    * the **ceiling** (``effective_timeout``) -- the schedule's ``timeout``, else the owning
      process's ``max_duration_seconds``, or ``streaming_max_duration_seconds`` when the target
      streams. Enforced as an absolute per-attempt cap via ``asyncio.wait_for``. It never
      resets, so it is sized as a backstop against a run that never ends rather than as a
      routine bound -- and only a streaming target, which has the idle bound watching it, gets
      the longer one.
    * the **idle bound** (``effective_idle_timeout``) -- the schedule's ``idle_timeout``, else
      the owning process's ``idle_timeout_seconds``. Enforced inside the streaming executors
      around each stream event, not here, because only they can observe progress.

    A failed attempt is retried only when its HTTP status code is in ``retry.retryable_errors``
    and retries remain; the orchestrator waits the corresponding ``interval_sequence`` entry
    before retrying. Timeouts and transport errors carry no status code and are therefore
    terminal -- that applies to both bounds.

    Each attempt, retry, and terminal outcome is recorded via structured logs; per-attempt
    detail is intentionally not persisted to the database. Attempt failures always log at
    warning level: every error reaching this orchestrator originates outside the scheduler
    (target status, unreachable URL, timeout), so error level stays reserved for our own faults.

    Returns:
        A :class:`RunOutcome` describing the final result and the number of attempts made.
    """
    worker_http = get_settings().worker_http
    defaults = ctx.timeouts or RunTimeouts(
        max_duration=worker_http.max_duration_seconds,
        streaming_max_duration=worker_http.streaming_max_duration_seconds,
        idle_timeout=worker_http.idle_timeout_seconds,
    )
    default_ceiling = defaults.streaming_max_duration if properties.is_streaming else defaults.max_duration
    effective_timeout = properties.timeout if properties.timeout is not None else default_ceiling
    effective_idle_timeout = properties.idle_timeout if properties.idle_timeout is not None else defaults.idle_timeout
    ctx.effective_timeout = effective_timeout
    ctx.effective_idle_timeout = effective_idle_timeout

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
            idle_timeout=effective_idle_timeout,
            retryable_errors=retryable_log,
        )

        try:
            result = await asyncio.wait_for(executor.execute(ctx, credentials, properties), timeout=effective_timeout)
        except asyncio.TimeoutError:
            error = ScheduleRunError(
                f"execution timed out after {effective_timeout}s",
                schedule_id=ctx.schedule_id,
                run_id=ctx.run_id,
            )
            logger.warning(
                AttemptEvent.TIMED_OUT,
                schedule_id=ctx.schedule_id,
                run_id=ctx.run_id,
                attempt=attempt,
                is_retry=is_retry,
                timeout=effective_timeout,
                idle_timeout=effective_idle_timeout,
            )
            return RunOutcome(succeeded=False, result=None, error=error, attempt_count=attempt)
        except StreamIdleTimeoutError as exc:
            # Terminal like the ceiling, but a different operational problem: the target was
            # reachable and simply stopped making progress. Logged under its own event so the
            # two are distinguishable without reading the message.
            logger.warning(
                AttemptEvent.IDLE_TIMED_OUT,
                schedule_id=ctx.schedule_id,
                run_id=ctx.run_id,
                attempt=attempt,
                is_retry=is_retry,
                timeout=effective_timeout,
                idle_timeout=effective_idle_timeout,
            )
            return RunOutcome(succeeded=False, result=None, error=exc, attempt_count=attempt)
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
                logger.warning(
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
                logger.warning(
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

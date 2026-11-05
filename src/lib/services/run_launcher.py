import asyncio
from typing import Any

import httpx
from opentelemetry import trace
from structlog.contextvars import bind_contextvars, clear_contextvars

from src.lib.db.engine import Database
from src.lib.exceptions.errors import ScheduleRunError
from src.lib.repositories.schedule_run_repository import ScheduleRunRepository
from src.lib.schemas.run import RunContext, RunTimeouts
from src.lib.utils.logging import logger
from src.worker.jobs import execute_run


_tracer = trace.get_tracer(__name__)

# How long a shutdown waits for in-flight on-demand runs before cancelling them.
DRAIN_TIMEOUT_SECONDS = 30.0

_SHUTDOWN_ERROR_TEXT = "run cancelled: the API service shut down before the target responded"


class BackgroundRunLauncher:
    """Runs on-demand schedule executions inside the API process.

    ``POST /schedules/{id}/run`` answers as soon as the run row exists, so the execution
    itself happens here rather than on the request's own task. Runs are executed with the
    API's HTTP client and ``API_HTTP_*`` timeouts — the worker is not involved and the
    schedule's next fire time is untouched.

    Every launched task is held in a strong reference: an ``asyncio`` task with no remaining
    reference can be garbage-collected mid-flight, which would abandon a run in
    ``in_progress`` forever.
    """

    def __init__(self, http_client: httpx.AsyncClient, timeouts: RunTimeouts) -> None:
        self._http_client = http_client
        self._timeouts = timeouts
        # task -> (run_id, schedule_id), so a shutdown can name the runs it is about to cancel.
        self._tasks: dict[asyncio.Task[None], tuple[str, str]] = {}

    @property
    def in_flight(self) -> int:
        """Number of runs currently executing."""
        return len(self._tasks)

    def launch(self, run_id: str, schedule_id: str, snapshot: dict[str, Any]) -> None:
        """Start executing an already-recorded run and return immediately."""
        task = asyncio.create_task(self._execute(run_id, schedule_id, snapshot))
        self._tasks[task] = (run_id, schedule_id)
        task.add_done_callback(lambda finished: self._tasks.pop(finished, None))

    async def _execute(self, run_id: str, schedule_id: str, snapshot: dict[str, Any]) -> None:
        clear_contextvars()
        bind_contextvars(schedule_id=schedule_id, run_id=run_id)
        logger.info("on_demand_run_start", run_id=run_id, schedule_id=schedule_id)
        try:
            with _tracer.start_as_current_span(f"run:on_demand:{schedule_id}"):
                ctx = RunContext(
                    run_id=run_id,
                    schedule_id=schedule_id,
                    http_client=self._http_client,
                    timeouts=self._timeouts,
                )
                await execute_run(ctx, snapshot)
        except ScheduleRunError:
            # Already persisted on the run record and logged by execute_run. Nothing upstream
            # is waiting on this task, so the error stops here rather than surfacing as an
            # unretrieved task exception.
            pass
        finally:
            clear_contextvars()

    async def drain(self, timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
        """Wait for in-flight runs to finish, then fail and cancel whatever is left.

        Called on shutdown so a run that is nearly done still records its own outcome. Runs
        still executing past ``timeout`` are marked as errored *before* being cancelled:
        ``CancelledError`` derives from ``BaseException``, so ``execute_run``'s handler would
        not see it and the run would otherwise stay ``in_progress`` forever.
        """
        if not self._tasks:
            return
        _, still_running = await asyncio.wait(list(self._tasks), timeout=timeout)
        if not still_running:
            return

        for task in still_running:
            run_id, schedule_id = self._tasks.get(task, ("unknown", "unknown"))
            logger.warning("on_demand_run_cancelled", run_id=run_id, schedule_id=schedule_id)
            await self._mark_cancelled(run_id, schedule_id)
            task.cancel()
        await asyncio.gather(*still_running, return_exceptions=True)

    @staticmethod
    async def _mark_cancelled(run_id: str, schedule_id: str) -> None:
        try:
            async with Database().sessionmaker() as session:
                await ScheduleRunRepository(session).mark_error(run_id, _SHUTDOWN_ERROR_TEXT)
        except Exception as exc:
            logger.error("run_persist_failed", run_id=run_id, schedule_id=schedule_id, error=str(exc))

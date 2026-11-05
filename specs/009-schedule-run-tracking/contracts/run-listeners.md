# Contract: Run Lifecycle Listeners

New module `src/worker/listeners.py`. A factory builds the two scheduler listeners, capturing a
task registry (no module-level globals — Constitution: FastAPI/worker components are not globals).

```python
def build_run_listeners(tracker: set[asyncio.Task[Any]]) -> tuple[
    Callable[[JobExecutionEvent], None],  # on_job_executed  (EVENT_JOB_EXECUTED)
    Callable[[JobExecutionEvent], None],  # on_job_error     (EVENT_JOB_ERROR)
]: ...
```

Registration (in `src/worker/app.py` `run_worker`/`_run`):

```python
tracker: set[asyncio.Task[Any]] = set()
on_executed, on_error = build_run_listeners(tracker)
scheduler.add_listener(on_executed, EVENT_JOB_EXECUTED)
scheduler.add_listener(on_error, EVENT_JOB_ERROR)
```

## `on_job_executed(event)` — EVENT_JOB_EXECUTED

1. If `event.retval` is **not** a `RunOutcome` → log `run_persist_skip` (debug/info) and return.
   (Heartbeat returns `None`; any non-tracked job is skipped — FR-002, SC-005.)
2. Otherwise schedule `_record_success(event.retval)` as a loop task; add it to `tracker`; attach
   a done-callback that discards from `tracker` and logs any task exception.

`_record_success(outcome)`:
- Open session → `ScheduleRunRepository.mark_success(outcome.run_id, outcome.result)`.
- If it returns `None` → log `run_record_missing` discrepancy (FR-010).
- Else log `run_recorded_success` with `run_id`, `schedule_id`.

## `on_job_error(event)` — EVENT_JOB_ERROR

1. Read `event.exception`.
2. If it is **not** a `ScheduleRunError` → log `job_error` with `event.job_id` (preserves the
   existing generic-error logging from feature 008) and return (no row to update).
3. Otherwise log `run_failed` with `schedule_id`/`run_id`/`error`, then schedule
   `_record_error(exc)` as a tracked loop task (same tracker/done-callback pattern).

`_record_error(exc)`:
- Open session → `ScheduleRunRepository.mark_error(exc.run_id, str(exc))`.
- If it returns `None` → log `run_record_missing` discrepancy (FR-010).
- Else log `run_recorded_error` with `run_id`, `schedule_id`.

> This module supersedes the standalone `on_job_error` previously in `app.py`; its logging
> behavior is folded into `on_job_error` here.

## Threading & reliability guarantees

- Listeners are **synchronous** and run **in the event loop thread** (see research.md); they only
  *schedule* async work via `asyncio.get_running_loop().create_task(...)` and never block.
- Every scheduled task is held in `tracker` until done (prevents premature GC) and its failures
  are logged in the done-callback.
- On shutdown, `run_worker` drains `tracker` with
  `await asyncio.gather(*tracker, return_exceptions=True)` so no terminal write is lost.
- A listener never raises out to APScheduler; persistence failures are caught and logged.

## Logged events (all via `src.lib.utils.logging.logger`)

| Event key | When | Key fields |
|-----------|------|-----------|
| `run_persist_skip` | non-RunOutcome retval (e.g. heartbeat) | `job_id` |
| `run_recorded_success` | success row updated | `run_id`, `schedule_id` |
| `run_failed` | error event received | `run_id`, `schedule_id`, `error` |
| `run_recorded_error` | error row updated | `run_id`, `schedule_id` |
| `run_record_missing` | terminal update found no row | `run_id`, `schedule_id` |
| `job_error` | non-ScheduleRunError failure | `job_id`, `error` |

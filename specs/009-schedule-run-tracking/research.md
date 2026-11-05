# Phase 0 Research: Schedule Run Tracking

All items below were resolved by reading the installed APScheduler 3.11.2 source and the
existing codebase; no `NEEDS CLARIFICATION` markers remained from the spec.

## Decision 1 — Where and how listeners run (event-loop thread)

**Decision**: Register two synchronous listeners (`EVENT_JOB_EXECUTED`, `EVENT_JOB_ERROR`).
Each schedules the async DB update via `asyncio.get_running_loop().create_task(coro)` and never
performs blocking I/O inline.

**Rationale**: For coroutine jobs, `AsyncIOExecutor._do_submit_job` uses
`self._eventloop.create_task(coro)` with a done-callback. That callback executes **in the event
loop thread** and calls `_run_job_success` / `_run_job_error` → `_dispatch_event`, which invokes
each registered listener **synchronously in the loop thread**
(`apscheduler/schedulers/base.py:_dispatch_event`). Because we are already on the running loop, a
listener can create a task directly. `_dispatch_event` also wraps each `cb(event)` in
`try/except` and logs "Error notifying listener", so a synchronous listener fault cannot crash
the scheduler.

**Alternatives considered**:
- *`asyncio.run_coroutine_threadsafe(coro, loop)`* — designed for cross-thread submission; calling
  it from within the loop thread is unnecessary and risks deadlock if `.result()` is ever awaited.
  Rejected: we are already in the loop thread.
- *Perform the DB write synchronously inside the listener* — would block the event loop
  (violates Constitution I). Rejected.
- *Make the persistence part of `execute_scheduled_job` itself (no listeners)* — the user
  explicitly specified the listener-based design, and listeners cleanly separate "produce
  outcome" from "persist outcome", and also capture failures raised out of the job. Adopted the
  listener design as specified.

## Decision 2 — Correlating the terminal update to the start row

**Decision**: Use the generated `run_id` (a `uuid4`) as the `schedule_runs.id` primary key. The
in-progress insert sets `id = run_id`; `mark_success`/`mark_error` fetch by `id = run_id`.

**Rationale**: The success object (`RunOutcome.run_id`) and the failure (`ScheduleRunError.run_id`)
both carry the same `run_id` the job generated. Keying the row by `run_id` makes the terminal
lookup an exact primary-key fetch (FR-007, FR-011) with no inference. `RunContext.run_id` is a
`str`; the repository converts to `uuid.UUID` at the boundary.

**Alternatives considered**:
- *Add a separate `run_id` column distinct from `id`* — redundant; the model already has a UUID
  primary key. Rejected (YAGNI).

## Decision 3 — No new migration

**Decision**: Reuse the existing `schedule_runs` table as-is.

**Rationale**: Migration `20260610_1947_092b5f51aa98` already creates the table and a `status`
CHECK constraint permitting `success`, `error`, `in_progress`, `missed`. The `ScheduleRun` model
already has `end_time` (nullable), `result` (JSONB, nullable), `schedule_id` (FK), and
`schedule_snapshot` (JSONB, not null). Nothing in the schema changes.

## Decision 4 — Result payload shape (executors return a JSON object)

**Decision**: Executors stop being `-> None` and **return `dict[str, Any]`** (a JSON object).
Per-executor shapes: REST → `{"status_code": int, "body": <json|text>}`; SSE → `{"events":
[{event,data}, ...]}`; chat_completion → `{"result": <completion>}` for non-streamed or
`{"chunks": [...]}` for streamed. `execute_scheduled_job` wraps this as `RunOutcome.result` and
the success listener stores it verbatim into `schedule_runs.result`.

**Rationale**: `schedule_runs.result` is typed `dict[str, Any]` (JSONB). The documentation
allows "accumulated list of values ... or direct response json"; wrapping list-shaped
accumulations under a key keeps the column a JSON object and keeps the typed contract a `dict`.
Executors already log per-chunk/per-event for observability — they now **also** accumulate and
return, so logging behavior from feature 008 is preserved.

**Alternatives considered**:
- *Widen `result` to accept a top-level JSON array* — would require changing the model's typed
  column and weaken the typed contract for marginal benefit. Rejected (KISS / Contract-Driven).

## Decision 5 — Skipping the heartbeat (no-op) job

**Decision**: The success listener records a run **only** when `event.retval` is a `RunOutcome`;
otherwise it logs a skip and returns. The heartbeat coroutine returns `None`, so it is naturally
skipped (FR-002, SC-005). The error listener records **only** when `event.exception` is a
`ScheduleRunError` (which carries `run_id`); other exceptions are logged but not persisted.

**Rationale**: Type-based discrimination is robust and avoids hard-coding job-id checks, while
still correctly excluding the heartbeat. It also gracefully handles any future non-tracked job.

## Decision 6 — Best-effort start persistence & discrepancy handling

**Decision**: Creating the `in_progress` row is wrapped in `try/except`; failure is logged and
the run proceeds (spec Assumptions). If a terminal update finds no row for a `run_id`
(`mark_success`/`mark_error` return `None`), the listener logs a "no matching run record"
discrepancy rather than raising (FR-010).

**Rationale**: The feature must not change whether the underlying task runs; and a missing start
row must surface as an observable discrepancy, never a silent drop or a crash.

## Decision 7 — Obtaining an async session in the worker

**Decision**: Persistence coroutines open a session via `Database().sessionmaker()` (the existing
singleton-backed async sessionmaker), instantiate `ScheduleRunRepository(session)`, perform the
write, and commit within an `async with` block.

**Rationale**: The worker is not a FastAPI app, so there is no `app.state`/DI session. `Database`
is the established shared engine/sessionmaker holder (`expire_on_commit=False`), reused here. No
new global is introduced.

## Decision 8 — Draining pending persistence on shutdown

**Decision**: `run_worker` owns a `set[asyncio.Task]` registry passed into the listener factory;
each scheduled update task adds/removes itself. On shutdown, after `stop_event` and before/around
`scheduler.shutdown(wait=True)`, `await asyncio.gather(*tracker, return_exceptions=True)` drains
in-flight updates.

**Rationale**: Listener-scheduled tasks are not tracked by APScheduler's own executor shutdown,
so without draining a success/error write could be lost if the process exits immediately
(Constitution I — "loosing data prevention"). The registry lives in `run_worker` scope (closure),
honoring the no-globals rule.

# Implementation Plan: Schedule Run Tracking

**Branch**: `009-schedule-run-tracking` | **Date**: 2026-06-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/009-schedule-run-tracking/spec.md`

## Summary

Persist the full lifecycle of every fired non-heartbeat scheduled job into the existing
`schedule_runs` table. When `execute_scheduled_job` begins, it writes an `in_progress` run row
(`end_time=None`, `result=None`, `schedule_id`, `schedule_snapshot=kwargs`) keyed by the
generated `run_id`. On success the job **returns** a standardized `RunOutcome`
(`run_id`, `schedule_id`, `result`); on failure it raises `ScheduleRunError`
(`run_id`, `schedule_id`) — both already flow through APScheduler. Two scheduler listeners
registered on `EVENT_JOB_EXECUTED` and `EVENT_JOB_ERROR` update the matching row to `success`
(with the produced `result`) or `error` (with `{"error_text": ...}`) and stamp `end_time`. The
executors are changed to **return** their accumulated result (instead of only logging it) so a
result payload exists to persist. Every lifecycle action is logged. The worker's own heartbeat
job returns `None` and is therefore skipped by the listeners — no row is created for it.

This feature closes the run-persistence gap that feature 008 explicitly deferred ("logging
only"), directly satisfying Constitution II ("all task runs persisted in DB, both successful
and failed").

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: APScheduler ~=3.11 (`AsyncIOScheduler`, `EVENT_JOB_EXECUTED`,
`EVENT_JOB_ERROR`, `JobExecutionEvent`), SQLAlchemy 2.0 async + SQLModel (reuse `src/lib/db`,
`Database` sessionmaker), Pydantic v2 (`RunOutcome`, reuse `RunContext`/`ScheduleProperties`),
`structlog` (`src.lib.utils.logging.logger`). No new third-party dependency.

**Storage**: PostgreSQL `schedule_runs` table — **already exists** (migration
`20260610_1947_092b5f51aa98`, model `src/lib/models/schedule_run.py`). The `status` CHECK
constraint already permits `in_progress`, `success`, `error`. **No new migration required.**

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`), `pytest-mock`,
`structlog.testing.capture_logs` for log assertions. Unit tests mock the session/repository;
one integration test fires a job end-to-end against a real `AsyncIOScheduler` and asserts the
row transitions `in_progress` → `success`/`error`.

**Target Platform**: Linux container (Docker), long-lived worker process; entry point
`entrypoint_worker.py` → `src.worker.app:run_worker`.

**Project Type**: Background worker service (`src/worker/`) depending on shared `src/lib/`.

**Performance Goals**: Persistence MUST NOT block the scheduling loop (Constitution I). All DB
writes are `async`. Listeners are synchronous APScheduler callbacks dispatched **in the event
loop thread** (confirmed below) — they schedule the async update as a loop task rather than
performing blocking I/O inline.

**Constraints**: No event-loop blocking; no module-level mutable globals (listeners capture
their task registry via a closure built in `run_worker`); secrets never persisted or logged
(FR-012); best-effort start persistence must not abort the run (spec Assumptions). Pending
persistence tasks are drained on shutdown to prevent data loss (Constitution I).

**Scale/Scope**: One heartbeat job (skipped); an unbounded number of task jobs, each producing
exactly one run row that reaches one terminal status.

### Key technical finding (drives the listener design)

`AsyncIOExecutor._do_submit_job` runs coroutine jobs via `loop.create_task(coro)` and attaches
a done-callback. That callback runs **in the event loop thread** and calls
`_run_job_success`/`_run_job_error` → `_dispatch_event`, which invokes each listener
**synchronously, in the loop thread**. Therefore a listener can safely call
`asyncio.get_running_loop().create_task(...)` to schedule the async DB update. `_dispatch_event`
wraps each listener call in `try/except` and logs "Error notifying listener", so a synchronous
listener exception cannot crash the scheduler — but errors inside the *scheduled* update task
are ours to catch and log. See [research.md](./research.md).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| **I. Reliability First** | ✅ Pass | All DB I/O `async`, never blocking the loop; `try/except` around every persistence step; start-record failure is logged and the run proceeds; pending update tasks are awaited (drained) on shutdown to prevent lost writes; terminal updates are idempotent-friendly (fetch-by-id then set). |
| **II. Observability** | ✅ Pass | **This feature removes the 008 deviation.** Every lifecycle action (create, success, error, no-op skip, no-matching-record discrepancy) emits a structured `logger` entry carrying `run_id`/`schedule_id`; all runs (success and failure) are now persisted in the DB as the constitution requires. |
| **III. Test-First** | ✅ Pass | TDD: failing unit tests for the repository, the two listeners (incl. heartbeat skip and discrepancy logging), and the `execute_scheduled_job`/executor return-value changes precede implementation; one integration test asserts the persisted row lifecycle. |
| **IV. Contract-Driven** | ✅ Pass | No REST API added. Internal worker↔persistence contracts are explicit typed Pydantic/typed interfaces: `RunOutcome` (success return), existing `ScheduleRunError` (failure), the executor return type, and the `ScheduleRunRepository` interface. Documented in `contracts/`. No ad-hoc dicts cross the boundary. |
| **V. Simplicity & YAGNI** | ✅ Pass | Reuses the existing model, enum, table, and migration. Adds exactly one repository, one listeners module, and one schema (`RunOutcome`). No new abstraction layer; functions kept to ≤2–3 params via Pydantic models. |

**Gate result**: PASS — no deviations. (Complexity Tracking intentionally empty.)

## Project Structure

### Documentation (this feature)

```text
specs/009-schedule-run-tracking/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (internal contracts)
│   ├── run-outcome.md          # RunOutcome success object + executor return type
│   ├── schedule-run-repository.md
│   └── run-listeners.md        # EVENT_JOB_EXECUTED / EVENT_JOB_ERROR listener contracts
└── checklists/
    └── requirements.md  # From /speckit-specify
```

### Source Code (repository root)

```text
src/
├── worker/
│   ├── app.py                    # MODIFY: register EVENT_JOB_EXECUTED + EVENT_JOB_ERROR
│   │                             #   listeners built by a closure factory (loop task
│   │                             #   registry); drain pending persistence tasks on shutdown
│   ├── listeners.py              # NEW: build_run_listeners(tracker) -> (on_executed,
│   │                             #   on_error); each schedules an async DB update coroutine
│   │                             #   (_record_success / _record_error) and logs every action
│   ├── jobs.py                   # MODIFY: create in_progress run at start (before validation);
│   │                             #   return RunOutcome on success; ScheduleRunError on failure
│   └── executors/
│       ├── base.py               # MODIFY: Executor.execute returns dict[str, Any] (result)
│       ├── rest.py               # MODIFY: return {"status_code", "body"} instead of only logging
│       ├── sse.py                # MODIFY: accumulate events; return {"events": [...]}
│       └── chat_completion.py    # MODIFY: accumulate; return {"result": ...} / {"chunks": [...]}
└── lib/
    ├── repositories/
    │   └── schedule_run_repository.py   # NEW: create_in_progress / mark_success / mark_error
    └── schemas/
        └── run.py                # MODIFY: add RunOutcome(run_id, schedule_id, result)

tests/
├── unit/
│   ├── lib/repositories/
│   │   └── test_schedule_run_repository.py   # NEW
│   └── worker/
│       ├── test_listeners.py                 # NEW: success/error persist, heartbeat skip,
│       │                                     #   no-matching-record discrepancy logging
│       ├── test_jobs.py                      # EXTEND: in_progress create + RunOutcome return
│       ├── test_worker_app.py                # EXTEND: both listeners registered; drain on stop
│       └── executors/
│           ├── test_rest.py                  # EXTEND: asserts returned result payload
│           ├── test_sse.py                   # EXTEND: asserts accumulated events returned
│           └── test_chat_completion.py       # EXTEND: asserts returned result payload
└── integration/
    └── test_schedule_run_lifecycle.py        # NEW: fire a job; assert row in_progress→success/error
```

**Structure Decision**: Extend the existing `src/worker/` service. Persistence orchestration
(listeners, in-progress creation) lives in `worker/` because it is wired to APScheduler events;
the reusable data-access piece (`ScheduleRunRepository`) and the `RunOutcome` schema live in
`src/lib/` (inward-only dependency: `worker/ → lib/`). The dispatch/listener path is the only
place that writes `schedule_runs`; the API service does not. Reuse `pyproject.toml`,
`Dockerfile`, `entrypoint_worker.py`, the `ScheduleRun` model, the `ScheduleRunStatus` enum, and
the existing migration unchanged.

## Complexity Tracking

> No constitution violations — table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _(none)_  | —          | —                                    |

## Phase 0 / Phase 1 outputs

- Phase 0 decisions: see [research.md](./research.md).
- Phase 1 design: see [data-model.md](./data-model.md), [contracts/](./contracts/),
  [quickstart.md](./quickstart.md).

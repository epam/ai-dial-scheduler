# Implementation Plan: APScheduler Executor Worker

**Branch**: `008-apscheduler-executor-worker` | **Date**: 2026-06-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-apscheduler-executor-worker/spec.md`

## Summary

Turn the existing placeholder worker (`src/worker/`) into a real APScheduler executor. When a
job fired by the shared `SQLAlchemyJobStore` invokes the dispatch handler, the handler
generates a `run_id`, gets task config from method kwargs, validates it with
the existing `ScheduleProperties` Pydantic model, and branches by `target_type` into one of
three executors (`chat_completion`, `rest`, `sse`) that share one interface and log results as
they arrive. Any failure raises a `ScheduleRunError` carrying `schedule_id` and `run_id`, which
an `EVENT_JOB_ERROR` listener logs. The worker also registers exactly one of its own jobs — a
no-op heartbeat on an interval — in a non-persistent in-memory jobstore so it never pollutes the
shared store. No result persistence: logging only.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: APScheduler ~=3.11 (`AsyncIOScheduler`, `SQLAlchemyJobStore`,
`MemoryJobStore`, `IntervalTrigger`, `EVENT_JOB_ERROR`), SQLAlchemy 2.0 async + SQLModel
(reuse `src/lib/db`), `httpx==0.28.1` (REST + SSE transport), **`openai`** (new —
`AsyncAzureOpenAI`), **`httpx-sse`** (new — SSE event iteration), Pydantic v2
(reuse `ScheduleProperties`), `structlog` (`src.lib.utils.logging.logger`), `aidial-sdk`
(`init_telemetry` for non-API service tracing per Constitution II).

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`), `pytest-mock`,
`structlog.testing.capture_logs` for log assertions. Unit tests mock httpx/openai/DB;
one integration test fires a job end-to-end against a real `AsyncIOScheduler`.

**Target Platform**: Linux container (Docker), long-lived process; entry point
`entrypoint_worker.py` → `src.worker.app:run_worker`.

**Project Type**: Background worker service (single repo, `src/worker/` entry point depending
on `src/lib/`).

**Performance Goals**: Action dispatch must not block the scheduling loop (Constitution I) —
all I/O is `async`; streaming results are logged per-chunk/per-event as they arrive, not
buffered. No throughput target beyond "scheduling lag in seconds, not minutes".

**Constraints**: No result persistence (FR-016). Configured request timeout on REST/SSE
(FR-018). `extra_headers` win on header collision. `dial_api_key` never logged. No
module-level mutable globals — shared `httpx.AsyncClient` lives in a lifecycle-managed
`WorkerContext`.

**Scale/Scope**: One heartbeat job owned by the worker; an unbounded number of task jobs
owned by the creator service. Three executors behind one dispatch entry point.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| **I. Reliability First** | ✅ Pass | All I/O `async` (no event-loop blocking); per-run `try/except` raising standardized `ScheduleRunError`; configured httpx timeout; non-2xx → failure. APScheduler `misfire_grace_time`/`max_instances=1` already set. |
| **II. Observability** | ⚠️ Conditional | Structured logs via `logger` for every heartbeat, run start, chunk/event, single result, and failure; `EVENT_JOB_ERROR` listener logs failures with `schedule_id`+`run_id`; `init_telemetry` (AiDialSdk) wired at startup for a non-API service. **Deviation**: Constitution says "all task runs should be persisted in DB"; this feature explicitly scopes out persistence (FR-016). See Complexity Tracking. |
| **III. Test-First** | ✅ Pass | TDD: failing unit tests for dispatcher, each executor, validation failures, the error listener, and the heartbeat precede implementation; integration test fires a job end-to-end. |
| **IV. Contract-Driven** | ✅ Pass | The worker exposes no REST API. Internal worker↔creator contract is expressed as typed Pydantic models (`ScheduleProperties`) and a documented dispatch import path + signature in `contracts/`. `ScheduleRunError` and the executor interface are explicit typed contracts. |
| **V. Simplicity & YAGNI** | ✅ Pass | Three executors behind one `Executor` interface + a `TargetType→Executor` map; no premature abstraction. The one added indirection (`WorkerContext`) replaces forbidden globals and is justified below. Functions kept to ≤2–3 params via Pydantic models. |

**Gate result**: PASS with one documented deviation (no run persistence — Complexity Tracking).

## Project Structure

### Documentation (this feature)

```text
specs/008-apscheduler-executor-worker/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (internal worker contracts)
│   ├── dispatch-handler.md
│   ├── kwargs-schema.md
│   └── executor-interface.md
└── checklists/
    └── requirements.md  # From /speckit-specify
```

### Source Code (repository root)

```text
src/
├── worker/                       # Worker service entry point (extend existing)
│   ├── app.py                    # MODIFY: add MemoryJobStore for heartbeat, register
│   │                             #   heartbeat job, attach EVENT_JOB_ERROR listener,
│   │                             #   init_telemetry, build/teardown WorkerContext
│   ├── context.py                # NEW: WorkerContext (shared httpx.AsyncClient) +
│   │                             #   lifecycle-managed accessor (set/get/clear)
│   ├── jobs.py                   # MODIFY: heartbeat handler; dispatch handler
│   │                             #   (run_id → validate kwargs → branch)
│   ├── dispatcher.py             # NEW: TargetType→Executor resolution
│   └── executors/                # NEW
│       ├── __init__.py
│       ├── base.py               # Executor protocol/ABC (common interface)
│       ├── chat_completion.py    # AsyncAzureOpenAI executor
│       ├── rest.py               # httpx REST executor
│       └── sse.py                # httpx-sse streaming executor
└── lib/
    ├── exceptions/__init__.py    # MODIFY: add ScheduleRunError(schedule_id, run_id)
    ├── schemas/run.py            # NEW: RunContext identity model (run_id + schedule_id)
    ├── settings.py               # MODIFY: WorkerSettings (noop_interval_seconds,
    │                             #   http_timeout_seconds) on Settings
    └── constants.py              # MODIFY: heartbeat job id/name, Accept header, Api-Key header

tests/
└── unit/worker/
    ├── test_worker_app.py        # EXTEND: heartbeat registration, listener wiring, jobstores
    ├── test_jobs.py              # REWRITE: heartbeat + dispatch behaviour
    ├── test_dispatcher.py        # NEW
    ├── test_context.py           # NEW
    └── executors/
        ├── __init__.py
        ├── test_chat_completion.py
        ├── test_rest.py
        └── test_sse.py
tests/integration/
└── test_worker_dispatch.py       # NEW: end-to-end fire of a job through the dispatcher
```

**Structure Decision**: Extend the existing `src/worker/` service rather than add a new
top-level layer. Execution logic lives entirely under `src/worker/` (the service entry point);
shared, reusable pieces (`ScheduleRunError`, `ScheduleProperties`, settings, constants) live
under `src/lib/`, honoring the constitution's inward-only dependency rule (`worker/ → lib/`,
never the reverse). The dispatch path does not read the database. Reuse `pyproject.toml`,
`Dockerfile`, and `entrypoint_worker.py` as-is.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **No run-result persistence** (deviates from Constitution II "all task runs persisted") | This feature is explicitly scoped to *execution + logging only* (FR-016, SC-007). Run-lifecycle persistence (the `schedule_runs` table) is owned by the surrounding service that consumes the standardized success/error objects this worker produces. | Persisting here would duplicate ownership of `schedule_runs`, couple the executor to write paths it doesn't own, and contradict the explicit spec scope. Deferred to the owning service; revisit if that ownership moves into the worker. |
| **`WorkerContext` lifecycle-managed accessor** (a process-level holder) | The worker has no FastAPI `app.state`; APScheduler invokes import-path handlers with kwargs only, so a shared `httpx.AsyncClient` needs a process-level home. | A bare module global is forbidden by the constitution; a fresh `httpx.AsyncClient` per run contradicts the spec's "shared client" and wastes connection pooling. The accessor is created in `run_worker` and torn down on shutdown — the worker analog of `app.state`. |

## Phase 0 / Phase 1 outputs

- Phase 0 decisions: see [research.md](./research.md).
- Phase 1 design: see [data-model.md](./data-model.md), [contracts/](./contracts/),
  [quickstart.md](./quickstart.md).

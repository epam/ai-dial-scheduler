# Implementation Plan: Schedule Management Endpoints

**Branch**: `007-schedule-endpoints` | **Date**: 2026-06-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/007-schedule-endpoints/spec.md`

## Summary

Implement four ownership-scoped schedule endpoints — Create, Update, List, Details — on the
FastAPI API service. Requests carry a `display_name`, exactly one trigger (`cron` **xor**
`date`), and a `properties` object validated per `target_type`. Create generates a `uuid4`
string id, persists the `schedules` row, then registers the job in the APScheduler
SQLAlchemyJobStore via `add_job` (id = schedule id, `replace_existing=True`, kwargs =
properties + `schedule_id`, trigger built from the request, `func` referenced by string path
to the worker job). Update mirrors this against an existing id. Because the ORM row and the
APScheduler job live in **two independent persistence mechanisms** (async ORM session vs.
APScheduler's own synchronous engine), atomicity is achieved with a **compensating
transaction**: persist/commit the row first, then `add_job`; on `add_job` failure, undo the
row change and re-raise. `next_run_time` is always read back from the `apscheduler_jobs`
engine table (epoch `Double` → aware UTC `datetime`) by schedule id. A placeholder worker job
`execute_scheduled_job(schedule_id, **properties)` logs the current UTC time and its kwargs.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI (via `aidial_sdk.DIALApp`), APScheduler 3.x (`AsyncIOScheduler` + `SQLAlchemyJobStore`), SQLModel/SQLAlchemy (async, asyncpg), Pydantic v2, structlog

**Storage**: PostgreSQL. Existing tables `schedules` (app-owned, from feature 006) and `apscheduler_jobs` (engine-owned). **No new tables or migrations** in this feature.

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server (Dockerized API service + worker service)

**Project Type**: Web service (single repo, `src/api` + `src/worker` + shared `src/lib`)

**Performance Goals**: Standard async web service; per-request work is one short DB transaction plus one jobstore write. No high-throughput target for this iteration.

**Constraints**: Async/await for all I/O; no blocking the event loop (the synchronous jobstore write is offloaded via `asyncio.to_thread`); no raw SQL (ORM/Core only); `lib/` must not import from `src.api`/`src.worker`; `logger` only (no `print()`); ≤ 2–3 params per function (use Pydantic models).

**Scale/Scope**: 4 endpoints, 1 placeholder worker job, ~1 repository, ~1 service, request/response Pydantic schemas, 3 new API-level enums. Owner identity hardcoded to `mock_user_id` this iteration.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Reliability First | Cross-store consistency handled explicitly; errors handled; no event-loop blocking; no data loss | **PASS** — compensating-transaction (saga) for the ORM↔jobstore pair; `add_job` offloaded via `asyncio.to_thread`; failures raise typed exceptions and roll the row back. See research R3/R4. |
| II. Observability (NON-NEGOTIABLE) | Every scheduling decision logs via `src.lib.utils.logging.logger` with structured fields; no `print()` | **PASS** — create/update/rollback/registration log with `schedule_id`, decision, reason; placeholder job logs via `logger` (the spec's "prints" is satisfied with `logger`, per this principle). |
| IV. Contract-Driven API | External REST contract is versioned (OpenAPI); API↔worker boundary is a typed Pydantic model, not ad-hoc dict | **PASS** — endpoints under `/v1` produce OpenAPI; the API↔worker payload shape is the shared `ScheduleProperties` Pydantic model in `lib/schemas`. Note: APScheduler serializes job kwargs as a dict on the wire; the model is the source of truth both sides reconstruct from. See research R6 + Complexity Tracking. |
| V. Simplicity & YAGNI | Simplest sufficient design; ≤3 params; no premature abstraction | **PASS** — one repository, one service; request models group params; `func` passed as a string reference (`"src.worker.jobs:execute_scheduled_job"`) so `lib/` needs no `worker` import. Only the four in-scope endpoints. |

**Layer rule check**: `lib/services` references the worker job by **string path** (constant in `lib/constants.py`), so the inward-only dependency arrow (`api`/`worker` → `lib`, never the reverse) is preserved. **PASS**

No violations requiring justification beyond the documented cross-store saga and the
dict-on-the-wire job kwargs (both in Complexity Tracking).

## Project Structure

### Documentation (this feature)

```text
specs/007-schedule-endpoints/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── schedule-endpoints.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── app.py                              # MODIFIED: include schedules router
│   ├── dependencies/
│   │   ├── db.py                           # existing get_db_session
│   │   └── scheduler.py                    # NEW: get_scheduler, get_schedule_service
│   └── v1/
│       └── endpoints/
│           └── schedules.py                # NEW: 4 route handlers (no logic)
├── worker/
│   └── jobs.py                             # MODIFIED: add execute_scheduled_job
└── lib/
    ├── constants.py                        # MODIFIED: MOCK_USER_ID, job func ref, defaults
    ├── db/
    │   └── enums.py                        # MODIFIED: TargetType, HttpMethod, TriggerType
    ├── exceptions/
    │   └── __init__.py                     # MODIFIED: ScheduleNotFoundError, JobRegistrationError
    ├── schemas/
    │   └── schedule.py                     # NEW: trigger/properties/request/response models
    ├── repositories/
    │   └── schedule_repository.py          # NEW: ORM CRUD + apscheduler_jobs LEFT JOIN
    └── services/
        └── schedule_service.py             # NEW: create/update/list/get + saga rollback

tests/
└── unit/
    ├── lib/
    │   ├── schemas/test_schedule_schemas.py        # NEW: trigger xor + properties rules
    │   ├── repositories/test_schedule_repository.py# NEW
    │   └── services/test_schedule_service.py       # NEW: rollback paths (mocked scheduler)
    ├── api/v1/endpoints/test_schedules.py          # NEW
    └── worker/test_jobs.py                         # NEW: execute_scheduled_job logs
```

**Structure Decision**: Web service layout from the constitution. New code is placed so the
inward dependency rule holds: route handlers in `src/api/v1/endpoints`, DI providers in
`src/api/dependencies`, all business logic + persistence in `src/lib` (service →
repository → ORM models), and the executed task in `src/worker`. The service reaches the
worker job only through a string reference constant, never an import.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Compensating transaction (saga) across `schedules` row and APScheduler jobstore | The two stores commit independently (async asyncpg session vs. APScheduler's own sync psycopg2 engine); a single DB transaction cannot span both. The spec mandates "rollback the row if `add_job` fails." | A single `async with session.begin()` wrapping both was rejected: `add_job` writes through APScheduler's private engine/connection and commits on its own, outside our session — so a session rollback would leave an orphan job. The saga is the minimal correct approach. |
| Job kwargs cross the API→worker boundary as a serialized dict | APScheduler persists `kwargs` as pickled data in the jobstore; it cannot carry a live Pydantic instance. | Passing a Pydantic model object directly is impossible across the serialized jobstore. We keep `ScheduleProperties` (a Pydantic model in `lib/schemas`) as the shared contract both sides validate against, satisfying Principle IV's intent while accepting dict serialization on the wire. |
| Reading `next_run_time` from the engine-owned `apscheduler_jobs` table via a Core `Table` LEFT JOIN | `next_run_time` is authoritative only inside APScheduler's store; the spec requires the response value to come from there. | Recomputing the trigger's next fire time in our code was rejected — it can diverge from what the engine actually persisted (misfire grace, paused state, replace semantics). Reading the stored value is the single source of truth. |

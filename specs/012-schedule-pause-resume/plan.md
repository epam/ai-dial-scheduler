# Implementation Plan: Schedule Pause & Resume Endpoints

**Branch**: `012-schedule-pause-resume` | **Date**: 2026-06-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/012-schedule-pause-resume/spec.md`

## Summary

Add two owner-scoped endpoints — `POST /schedules/{schedule_id}/pause` and
`POST /schedules/{schedule_id}/resume` — that stop and restart firing of an existing schedule
by pausing/resuming its APScheduler job. The schedule's identifier is already the APScheduler
job id, so the action targets the job directly. Ownership is enforced exactly as the existing
schedule read/update endpoints: a schedule that does not exist or belongs to another user is
reported as **404** (no existence disclosure). No database schema, columns, or migrations are
introduced — only the scheduler's shared job store row's `next_run_time` changes. Both actions
are idempotent and return the affected schedule (`ScheduleResponse`) with a refreshed
`next_run_time`.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI (via `aidial_sdk.DIALApp`), APScheduler 3.11
(`AsyncIOScheduler`), SQLAlchemy/SQLModel, Pydantic v2

**Storage**: PostgreSQL. The APScheduler `SQLAlchemyJobStore` (`apscheduler_jobs` table) is
shared between the API and worker processes; pause/resume mutate the job row's
`next_run_time` there. The `schedules` table is **not** modified.

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server (Docker), API service entry point (`src/api`)

**Project Type**: Web service (API + background worker; this feature touches the API only)

**Performance Goals**: Standard API latency; the blocking APScheduler job-store call is
offloaded with `asyncio.to_thread` to avoid blocking the event loop (Constitution I).

**Constraints**: No new persistence/migration; no global mutable state (scheduler lives in
`app.state`); `async`/`await` for all I/O; structured logging only (no `print()`).

**Scale/Scope**: Two endpoints, two new service methods, one error→HTTP mapping reuse, one
contract document. Single schedule per request (no bulk operations).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Compliance |
|-----------|------------|
| **I. Reliability First** | Blocking `pause_job`/`resume_job` job-store calls are run via `asyncio.to_thread` (no event-loop blocking). `JobLookupError` is handled and mapped to 404. No data loss: schedule rows are never deleted or mutated. |
| **II. Observability** | Each pause/resume emits a structured `logger` entry including `schedule_id`, the decision type (`schedule_paused` / `schedule_resumed`), and a reason code on rejection (`not_found`). No secrets/payloads logged. Tracing/metrics inherited from `DIALApp`. |
| **III. Test-First (NON-NEGOTIABLE)** | Unit tests for `ScheduleService.pause`/`resume` (owned, not-owned, missing-job, idempotent) and integration tests for both endpoints (200, 404 cross-owner, 404 unknown, 401 unauthenticated) are written and confirmed failing before implementation. |
| **IV. Contract-Driven API** | New endpoints documented in `contracts/pause-resume-endpoints.md`; FastAPI OpenAPI auto-generated. Response reuses the existing `ScheduleResponse` Pydantic v2 schema — no ad-hoc dicts. No breaking change to existing contracts (these were previously listed "out of scope"). |
| **V. Simplicity & YAGNI** | Reuses existing repository (`ScheduleRepository.get`), service, dependencies, auth, and error→HTTP mapping. Two thin service methods + two thin route handlers. No new abstraction, schema, or storage. Service methods take ≤2 params. |

**Result**: PASS. No violations; Complexity Tracking table not required.

## Project Structure

### Documentation (this feature)

```text
specs/012-schedule-pause-resume/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── pause-resume-endpoints.md
├── checklists/
│   └── requirements.md  # Created by /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── api/
│   └── v1/endpoints/
│       └── schedules.py            # ADD: pause_schedule, resume_schedule route handlers
└── lib/
    └── services/
        └── schedule_service.py     # ADD: pause(), resume() methods

tests/
├── unit/lib/services/
│   └── test_schedule_service.py    # ADD: pause/resume unit cases (mocked scheduler + repo)
└── integration/
    └── test_schedules_api.py       # ADD: pause/resume endpoint cases
```

**Structure Decision**: Web-service layout per the constitution (`src/api` + `src/lib`).
This feature modifies only two existing source files (`schedules.py`, `schedule_service.py`)
and two existing test files. No new modules, repositories, schemas, dependencies, or
migrations are added — the schedule id is already the APScheduler job id, the scheduler is
already exposed via `app.state` and the `get_schedule_service` dependency, ownership scoping
already exists in `ScheduleRepository.get`, and `ScheduleNotFoundError`→404 mapping already
exists in the endpoint module.

## Complexity Tracking

> No Constitution violations — table intentionally omitted.

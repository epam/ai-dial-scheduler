# Implementation Plan: Schedule Run List & Run Details Endpoints

**Branch**: `010-schedule-run-endpoints` | **Date**: 2026-06-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/010-schedule-run-endpoints/spec.md`

## Summary

Add two read-only, owner-scoped REST endpoints over the existing `schedule_runs` data:

- `GET /v1/schedules/{schedule_id}/runs` — paginated run history (id, status, start_time, end_time).
- `GET /v1/schedules/{schedule_id}/runs/{run_id}` — full run detail (status, timing, result/reason,
  plus an embedded schedule summary: display_name, trigger, properties).

Technical approach: follow the established schedule-endpoint stack exactly — a new
`runs` router, a new `ScheduleRunService` that authorizes via the existing
`ScheduleRepository.get(schedule_id, created_by)` ownership check, two new read methods on the
existing `ScheduleRunRepository`, and new Pydantic response schemas. Pagination reuses the existing
`Pagination` dependency / `Paginator` envelope. No new storage, no migration — this is a read layer
over data already produced by feature 009 (Schedule Run Tracking).

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI (via `aidial-sdk` `DIALApp`), SQLAlchemy/SQLModel, Pydantic v2,
APScheduler (already wired; not used by these read endpoints)

**Storage**: PostgreSQL — existing `schedule_runs` and `schedules` tables (read-only here; no new
tables, no Alembic migration)

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock; httpx `AsyncClient` +
`ASGITransport` for integration tests

**Target Platform**: Linux server (Docker container — API service)

**Project Type**: Web service (API service under `src/api/`, shared logic under `src/lib/`)

**Performance Goals**: Standard interactive API latency; list queries are a single indexed
count + page query (existing `IX_schedule_runs_schedule_id` index covers the `schedule_id` filter)

**Constraints**: Owner-scoped access (must be indistinguishable from "not found" across owners);
read-only (no state mutation); no secrets in logs; async I/O throughout; mypy + ruff gates must pass

**Scale/Scope**: Two endpoints, one new router, one new service, two new repository methods,
three new response schemas, one new exception. Bounded, additive change.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| **I. Reliability First** | PASS | Read-only; no data mutation, no retries needed. Not-found and malformed-input paths explicitly handled. Async DB I/O, no event-loop blocking. |
| **II. Observability** | PASS | Each access logs requester + schedule_id (+ run_id) via `src.lib.utils.logging.logger`; no secrets/result payloads logged. DIALApp telemetry already wraps the API. |
| **III. Test-First** | PASS (process) | TDD enforced at `/speckit-implement`: unit tests (endpoint w/ mocked service, service w/ mocked repos, repo w/ session) + integration tests written and failing before code. |
| **IV. Contract-Driven API** | PASS | New endpoints documented in `contracts/` and auto-published via FastAPI OpenAPI; request/response are explicit Pydantic v2 models. Additive (no breaking change). |
| **V. Simplicity & YAGNI** | PASS | Reuses existing pagination, ownership filter, repository/service/endpoint patterns. No new abstraction. Services keep ≤3 params (grouped via `Paginator`). No filtering/search (out of scope). |

**Structure/Convention gates**: snake_case/PascalCase, absolute imports, Google docstrings,
`lib/` does not import from `api/`, empty `__init__.py`, line-length 120 — all satisfied by the
planned layout below. No constitution violations → Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/010-schedule-run-endpoints/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── list-runs.md
│   └── run-details.md
└── checklists/
    └── requirements.md  # From /speckit-specify
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── app.py                                  # MODIFY: include runs_router under /v1
│   ├── dependencies/
│   │   └── runs.py                             # NEW: get_schedule_run_service dependency
│   └── v1/
│       └── endpoints/
│           └── runs.py                         # NEW: list_runs + get_run handlers
└── lib/
    ├── exceptions/
    │   └── errors.py                           # MODIFY: add ScheduleRunNotFoundError
    ├── repositories/
    │   └── schedule_run_repository.py          # MODIFY: add list_for_schedule + get_for_schedule
    ├── schemas/
    │   └── run.py                              # MODIFY: add RunListItem, RunResponse, RunScheduleSummary
    └── services/
        └── schedule_run_service.py             # NEW: ScheduleRunService (list_runs, get_run)

tests/
├── unit/
│   ├── api/v1/endpoints/test_runs.py                     # NEW: endpoint tests (mocked service)
│   ├── lib/services/test_schedule_run_service.py         # NEW: service tests (mocked repos)
│   └── lib/repositories/test_schedule_run_repository.py  # MODIFY: add list/get tests
└── integration/
    └── test_runs_api.py                                  # NEW: end-to-end over real DB
```

**Structure Decision**: Web-service layout already in place (`src/api/` + `src/lib/`). This feature
adds a sibling router (`runs.py`) and a dedicated service, mirroring the existing `schedules.py`
endpoint / `ScheduleService` pair. Ownership authorization is delegated to the existing
`ScheduleRepository.get(schedule_id, created_by)` so the created_by rule lives in exactly one place
(DRY). The new dependency `get_schedule_run_service` constructs both `ScheduleRepository` and
`ScheduleRunRepository` from the same `AsyncSession`.

## Complexity Tracking

No constitution violations — section intentionally empty.

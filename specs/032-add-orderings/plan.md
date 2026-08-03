# Implementation Plan: Add Ordering for Schedules/Runs List

**Branch**: `032-add-orderings` | **Date**: 2026-08-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/032-add-orderings/spec.md`

## Summary

Add caller-selectable ordering to the two existing list endpoints. The schedules list
(`GET /v1/schedules`) gains ordering by **creation date** (asc/desc), by **next run date**
(asc/desc) — where schedules with no next run time (paused/inactive jobs, which APScheduler
represents as `next_run_time IS NULL`) are always placed **last** regardless of direction — and by
**name** (asc/desc, case-insensitive on `display_name`). The runs list
(`GET /v1/schedules/{schedule_id}/runs`) gains ordering by **creation date** (asc/desc); runs have
no name field and are always scoped to one schedule, so name ordering is intentionally not offered
there.

Ordering is a per-request query parameter (`order_by`, `order_dir`) parsed by a typed FastAPI
dependency (mirroring the existing `Pagination` dataclass), threaded through the service layer as
a small Pydantic value object, and applied in the repository `.order_by(...)` clauses that today
hardcode `created_at DESC`. Next-run ordering sorts on the outer-joined
`apscheduler_jobs.c.next_run_time` column with `nulls_last()`; name ordering sorts on
`func.lower(display_name)`. Every ordering appends the primary key as a final tiebreaker so
pagination stays stable. No schema change, no migration.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, SQLAlchemy/SQLModel, Pydantic v2, APScheduler (no new deps —
`nulls_last`/`asc`/`desc` are stdlib SQLAlchemy)

**Storage**: PostgreSQL. No schema change — ordering keys are the existing `schedules.created_at`,
`schedule_runs.created_at`, and the engine-owned `apscheduler_jobs.next_run_time` already
outer-joined in `ScheduleRepository.list`. No migration required.

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock; integration tests
against PostgreSQL for real ordering (incl. NULL placement).

**Target Platform**: Linux server (API service container)

**Project Type**: Web service (API + background worker sharing `lib/`); this feature touches the
API service and `lib/` only.

**Performance Goals**: Negligible. Ordering is applied in-database on already-selected rows; the
sort columns are low-cardinality-safe (`created_at`, PK, joined `next_run_time`). No N+1, no extra
round trips.

**Constraints**: Deterministic, stable order across paginated pages (FR-006, SC-004) via a PK
tiebreaker; NULL next-run rows always last in both directions (FR-003, SC-002); unsupported
`order_by`/`order_dir` values rejected with a clear 422 (FR-007, SC-005); ordering composes with
existing metadata/name filtering and limit/offset pagination without changing matched rows
(FR-008).

**Scale/Scope**: Small, localized. One new `SortDirection` StrEnum + two feature sort-field
enums (`ScheduleSortField` with `created_at`/`next_run_time`/`name`, `RunSortField` with
`created_at`), one new query-param dependency (or two), a `SortSpec`-style Pydantic value object
threaded through two service methods, and parameterized `.order_by(...)` in two repository methods.
The paginator link builder is extended to echo the sort params so `next`/`previous` preserve order.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Reliability First | ✅ PASS | Pure query construction; no new I/O. NULL next-run handled explicitly with `nulls_last()` so paused schedules never sort unpredictably. PK tiebreaker guarantees deterministic pagination (no skipped/duplicated rows). Invalid params rejected before any query runs. |
| II. Observability | ✅ PASS | Existing list logging retained; the chosen ordering is added to the existing `logger.info` list calls as structured fields (order_by, order_dir). No new decision points; no secrets/content logged. |
| III. Test-First | ✅ PASS | Enum + dependency parsing + repository ordering (incl. NULLs-last and tiebreaker) are all unit/integration-testable; TDD applies — failing tests first. |
| IV. Contract-Driven API | ✅ PASS | New query params extend the existing OpenAPI contract for both GET endpoints (additive, non-breaking; defaults preserve current behavior). The internal ordering value object is a typed Pydantic model — no ad-hoc dict across the api→lib boundary. Contract documented in `contracts/`. |
| V. Simplicity & YAGNI | ✅ PASS | Only the fields the spec asks for are exposed (schedules: created_at + next_run_time + name; runs: created_at). Name ordering is offered on schedules only — runs have no name field and are single-schedule scoped, so it is intentionally excluded. No generic multi-column sort framework. Service methods keep ≤3 params by passing one `SortSpec` value object. Direction/field modeled as `StrEnum` following the existing `enums.py` convention. |

**Result**: PASS — no violations. Complexity Tracking not required.

**Post-Design Re-check**: PASS — Phase 1 artifacts introduce no new violations (see bottom).

## Project Structure

### Documentation (this feature)

```text
specs/032-add-orderings/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── schedules-list-ordering.md
│   └── runs-list-ordering.md
├── checklists/
│   └── requirements.md  # from /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── dependencies/
│   │   └── ordering.py              # NEW: typed Query-param dependencies →
│   │                                #   ScheduleOrdering / RunOrdering value objects
│   │                                #   (mirrors dependencies/pagination.py)
│   └── v1/endpoints/
│       ├── schedules.py             # list_schedules += ScheduleOrderingDep, passed to service.list
│       └── runs.py                  # list_runs += RunOrderingDep, passed to service.list_runs
└── lib/
    ├── db/
    │   └── enums.py                 # + SortDirection, ScheduleSortField, RunSortField (StrEnum)
    ├── schemas/
    │   └── ordering.py              # NEW: ScheduleOrdering / RunOrdering Pydantic value objects
    ├── services/
    │   ├── schedule_service.py      # list() accepts ordering, forwards to repo; logs order fields
    │   └── schedule_run_service.py  # list_runs() accepts ordering, forwards to repo
    └── repositories/
        ├── schedule_repository.py   # list(): parameterized .order_by (created_at | next_run_time
        │                            #   with nulls_last | lower(display_name)) + id tiebreaker
        └── schedule_run_repository.py # list_for_schedule(): parameterized .order_by(created_at)+id

tests/
├── unit/
│   ├── api/
│   │   └── dependencies/
│   │       └── test_ordering.py     # NEW: param parsing, defaults, invalid value → error
│   └── lib/
│       └── repositories/
│           ├── test_schedule_repository.py     # + ordering cases (both fields, both dirs, NULLs last)
│           └── test_schedule_run_repository.py  # + created_at asc/desc ordering cases
└── integration/
    ├── test_schedule_repository.py  # + real-DB ordering incl. next_run_time NULLs-last + tiebreaker
    ├── test_schedules_api.py        # + endpoint order_by/order_dir behavior + invalid → 422
    └── test_schedule_run_repository.py (or test runs API) # + runs ordering end-to-end
```

**Structure Decision**: Existing single web-service layout. Query-param parsing lives in
`src/api/dependencies/ordering.py` (API concern, like `pagination.py`); the resulting ordering
choice is carried inward as a typed Pydantic value object in `src/lib/schemas/ordering.py`; the
enums live in `src/lib/db/enums.py` next to `ScheduleRunStatus`; ordering is finally applied in the
two repository methods. `lib/` imports nothing from `api/` — the dependency arrow flows inward, and
the value object is defined in `lib/` and merely constructed by the `api/` dependency.

## Complexity Tracking

> No Constitution Check violations — table intentionally empty.

## Post-Design Constitution Re-check

- **IV. Contract-Driven**: New `order_by`/`order_dir` query params are additive and default to the
  current behavior (created_at desc), so no major version bump; both endpoints' contracts are
  documented under `contracts/`. The api→lib boundary uses the `ScheduleOrdering`/`RunOrdering`
  Pydantic value objects, not dicts. ✅
- **V. YAGNI**: Only spec-required sort fields are exposed; no generic sort DSL. One value object
  keeps service signatures at ≤3 params. ✅
- **III. Test-First**: `data-model.md` + `contracts/` enumerate exact cases (both directions, NULL
  placement, tiebreaker stability, invalid-value rejection, filter+order composition) to drive
  failing tests first. ✅

**Result**: PASS.

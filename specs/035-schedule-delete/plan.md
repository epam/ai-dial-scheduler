# Implementation Plan: Support Schedules Delete

**Branch**: `035-schedule-delete` | **Date**: 2026-08-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/035-schedule-delete/spec.md`

## Summary

Add `DELETE /v1/schedules/{schedule_id}` with a two-branch outcome driven by run history, plus an
`is_deleted` flag on schedules that is readable but never writable through the request body.

The deletion algorithm is **unregister first, then persist**: remove the APScheduler job (so the
schedule can no longer fire), then either hard-delete the row (no runs) or set `is_deleted = true`
(runs exist). This order is forced by FR-017 — a failed persist leaves a schedule that is *presented
as live but cannot fire*, which a retried `DELETE` can still fix, whereas the reverse order would
leave a schedule *marked deleted but still firing*, which the FR-015 "already deleted" guard makes
un-retryable. `JobLookupError` on unregister is treated as success (idempotent), covering the
"job missing from the engine" edge case.

The branch is chosen by an explicit run count (`ScheduleRunRepository.count_for_schedule`), and the
existing `schedule_runs.schedule_id → schedules.id ON DELETE RESTRICT` foreign key acts as the race
safety net: if a run is inserted between the count and the delete, the hard delete raises
`IntegrityError` and the service falls back to the soft-delete branch, preserving the new run
instead of failing the request.

`is_deleted` is exposed on `ScheduleResponse` and `ScheduleListItem`, and the schedules list gains an
`is_deleted` boolean query parameter defaulting to `false`, so the default view shows only live
schedules. Soft-deleted schedules stay retrievable via `GET /{schedule_id}` and keep their run
history reachable — the runs endpoints resolve the schedule through `ScheduleRepository.get`, which
does not filter on `is_deleted`, so FR-016 needs no code change. `update`, `pause`, `resume`, and a
second `delete` are guarded against soft-deleted schedules with a new `ScheduleAlreadyDeletedError`
→ **HTTP 409**. A worker-side pre-flight guard in `execute_scheduled_job` skips (and records no run
for) a schedule that is missing or deleted, closing the cross-process window in which the worker has
already fetched a due job that the API has since unregistered.

One migration adds the column: `NOT NULL` with `server_default false`, which backfills existing rows
to `false` in the same statement.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, SQLAlchemy/SQLModel, Pydantic v2, APScheduler, Alembic. No new
dependencies — `remove_job` and `JobLookupError` are already-imported APScheduler surface.

**Storage**: PostgreSQL. One additive schema change: `schedules.is_deleted BOOLEAN NOT NULL DEFAULT
false`. No new index (see research.md D6). The engine-owned `apscheduler_jobs` table is mutated only
through APScheduler's `remove_job`, never by DDL or direct DML.

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock. Unit tests for the
service branch selection, guards, and failure ordering (mocked scheduler + repos); integration tests
against PostgreSQL for the FK-RESTRICT fallback, the migration backfill, and end-to-end endpoint
behaviour.

**Target Platform**: Linux server — API service container (endpoint, service, repository) and worker
container (pre-flight guard).

**Project Type**: Web service (API + APScheduler worker sharing `lib/`). This feature touches
`api/`, `lib/`, `worker/`, and `migrations/`.

**Performance Goals**: A delete is 1 read + 1 count + 1 scheduler call + 1 write — constant work,
independent of run-history size (SC-007). The added `is_deleted` list predicate is a boolean
equality on an already-filtered, owner-scoped query. The worker guard adds one primary-key lookup
per run.

**Constraints**:
- No success response unless the schedule both cannot fire and is recorded as deleted/removed
  (FR-017, SC-006) → unregister-before-persist ordering, `JobRemovalError` → 502 on scheduler failure.
- Exactly one of two concurrent deletes wins → both writes are conditional (`WHERE ... AND
  is_deleted = false`) and decided by `rowcount`, not by a read-then-write.
- Run history is never mutated or deleted (FR-016) — enforced by the FK and by never touching
  `schedule_runs` on the write path.
- `is_deleted` is unwritable via the API (FR-009) — it is absent from `ScheduleRequest`, and
  Pydantic's default `extra="ignore"` drops it from the body.
- Deletion is irreversible (FR-014) — no code path clears `is_deleted`; the list filter only selects.

**Scale/Scope**: Small and localized. One column + migration, two response schemas gain a field, one
new endpoint, one new query parameter, one new service method plus three guards, three new
repository methods, one new exception, one worker guard.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Reliability First | ✅ PASS | Two-store operation (APScheduler job store + `schedules`) is ordered so the only reachable inconsistency is the benign, retry-fixable one, and the failure is reported rather than swallowed (FR-017). Concurrency is handled by conditional UPDATE/DELETE `rowcount` rather than read-then-write. Run history loss is structurally impossible: the FK is `ON DELETE RESTRICT`, so the database refuses to drop a schedule with runs even if application logic is wrong. All scheduler calls stay off the event loop via `asyncio.to_thread`, matching `pause`/`resume`. |
| II. Observability | ✅ PASS | Every attempt logs a structured event via `src.lib.utils.logging` with `schedule_id`, the branch taken (`removed` vs `marked_deleted`), `run_count`, and a `reason` on failure (FR-018, SC-008). Distinct events for the job-unregister step, the persist step, the FK fallback, and the worker skip. No secrets or payload content logged; `print()` unused. |
| III. Test-First | ✅ PASS | Every branch is independently testable: both delete paths, `JobLookupError` idempotency, scheduler failure → 502, persist failure → retryable, FK-RESTRICT fallback, the three already-deleted guards, list filter defaults, and `is_deleted` ignored in the request body. `data-model.md` and `contracts/` enumerate the exact cases so failing tests can be written first. |
| IV. Contract-Driven API | ✅ PASS | The new endpoint, the new query parameter, and the new response field extend the FastAPI-generated OpenAPI contract additively — no existing field or status code changes, so no major version bump. The api→lib boundary carries typed values (`bool`, `ScheduleResponse`), never ad-hoc dicts. Contracts documented under `contracts/`. |
| V. Simplicity & YAGNI | ✅ PASS | A single boolean, not a status enum or a `deleted_at`/`deleted_by` audit trio — the spec asks for exactly one bit and forbids restore, so richer state has no second use case. A two-valued list filter, no "all" option. No purge job, no retention policy, no cascade. New repository methods do one statement each. `ScheduleService.__init__` grows to 3 arguments (repository, run repository, scheduler), within the ≤3 limit. |

**Result**: PASS. One pre-existing parameter-count drift on the two `list` methods is carried
forward and recorded in [Complexity Tracking](#complexity-tracking); no new violation is introduced
by this feature.

**Post-Design Re-check**: PASS — see [Post-Design Constitution Re-check](#post-design-constitution-re-check).

## Project Structure

### Documentation (this feature)

```text
specs/035-schedule-delete/
├── plan.md              # This file
├── research.md          # Phase 0 output — 9 design decisions
├── data-model.md        # Phase 1 output — column, states, layer contracts
├── quickstart.md        # Phase 1 output — validation guide
├── contracts/           # Phase 1 output
│   ├── schedule-delete.md          # DELETE /v1/schedules/{schedule_id}
│   ├── schedule-is-deleted-field.md # is_deleted on list/details + PUT immutability
│   └── schedules-list-deleted-filter.md # is_deleted query param on the list
├── checklists/
│   └── requirements.md  # from /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── api/
│   └── v1/endpoints/
│       └── schedules.py                  # + DELETE /{schedule_id} → 204 (appended at EOF, after
│                                         #   resume — MUST NOT be inserted above either
│                                         #   `router.routes[-1].description +=` line, which
│                                         #   indexes the most recently added route)
│                                         # + is_deleted: bool = False query param on list_schedules
│                                         # + 409 mapping for ScheduleAlreadyDeletedError on
│                                         #   PUT / pause / resume / DELETE
│                                         # + 502 mapping for JobRemovalError on DELETE
└── lib/
    ├── models/
    │   └── schedule.py                   # + is_deleted: bool (NOT NULL, server_default false)
    ├── schemas/
    │   └── schedule.py                   # + is_deleted on ScheduleResponse and ScheduleListItem
    │                                     #   (ScheduleRequest deliberately unchanged — FR-009)
    ├── exceptions/
    │   └── errors.py                     # + ScheduleAlreadyDeletedError, JobRemovalError
    ├── services/
    │   └── schedule_service.py           # + delete(); + is_deleted guard in update/pause/resume;
    │                                     #   + is_deleted threaded through list() and echoed into
    │                                     #   the pagination links; _to_response maps the new field
    └── repositories/
        ├── schedule_repository.py        # + delete_owned() → bool (owner-scoped hard delete)
        │                                 # + mark_deleted() → bool (conditional soft delete)
        │                                 # + list(..., is_deleted) predicate on count and page
        │                                 #   (existing unscoped delete() kept for create rollback)
        └── schedule_run_repository.py    # + count_for_schedule(); list_for_schedule reuses it
worker/
└── src/worker/jobs.py                    # + pre-flight guard: skip execution and create no run
                                          #   record when the schedule is missing or is_deleted
migrations/versions/
└── 20260810_1200_e5f3c4d6a7b8_add_is_deleted_to_schedules.py   # NEW (down_revision d4e2b3c5f6a7)

documentation/
├── api_contract.md                       # DONE in this plan: Delete Schedule section (replaces the
│                                         #   pre-existing TODO), is_deleted on all four response
│                                         #   shapes, is_deleted list query param, 409 notes on
│                                         #   PUT/pause/resume. Verify against the implementation.
└── db_tables.md                          # DONE in this plan: schedules.is_deleted column

tests/
├── unit/
│   ├── api/v1/endpoints/test_schedules.py        # + DELETE 204/404/409/502; list is_deleted param
│   ├── api/mcp/test_mcp_tools.py                # + the auto-exposed delete tool
│   ├── lib/models/test_schedule.py              # + is_deleted column definition/default
│   ├── lib/schemas/test_schedule_schemas.py     # + is_deleted on responses; ignored on request
│   ├── lib/services/test_schedule_service.py    # + both branches, guards, failure ordering
│   ├── lib/repositories/test_schedule_repository.py     # + new methods, list filter
│   └── lib/repositories/test_schedule_run_repository.py # + count_for_schedule
└── integration/
    ├── test_schedules_api.py               # + end-to-end delete of a never-run schedule
    ├── test_schedule_delete.py             # NEW: soft-delete with runs, history survives,
    │                                       #   FK-RESTRICT fallback, guards, list filtering
    └── test_migrations.py (or equivalent)  # + is_deleted backfills to false on existing rows
```

**Structure Decision**: Existing single web-service layout, unchanged. Query-parameter parsing stays
in the endpoint (a plain `bool` needs no dependency module, unlike `pagination.py`/`ordering.py`);
the two-branch decision and the store-ordering guarantee live in `ScheduleService`; each repository
method remains a single statement with no business logic. `lib/` continues to import nothing from
`api/` or `worker/`. The worker guard lives in `worker/` and calls the existing
`ScheduleRepository.get_by_id`, adding no new shared surface.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `ScheduleService.list` takes 6 parameters and `ScheduleRepository.list` takes 7, against Constitution V's "at most 2–3 parameters" | Pre-existing drift from features 019 (metadata filter), 030 (name filter), and 032 (ordering), each of which added a keyword-only parameter. This feature adds one more, `is_deleted: bool = False`. | Folding the filters into a `ScheduleListFilters` Pydantic model is the right fix, but it would touch three unrelated features' call sites and tests inside a delete feature. Recorded here rather than silently ignored; the refactor belongs in its own change. Adding a single boolean does not make the existing signature meaningfully harder to read. |
| `ScheduleService.__init__` takes 3 arguments (repository, run repository, scheduler) | The run count selects the delete branch (FR-005) and must be logged (FR-018). | At the 2–3 limit, not over it. Passing the count in from the endpoint would leak business logic into the API layer. |

## Post-Design Constitution Re-check

- **I. Reliability**: Phase 1 pinned the store-mutation order and made both persist paths
  conditional single statements, so the design has no read-then-write race and no path that can
  destroy run history. The residual inconsistency window (job removed, row not yet updated) is
  documented, reported as an error, and closed by a retry. ✅
- **II. Observability**: `data-model.md` fixes the log event names and their required fields,
  including the branch taken, so SC-008 is verifiable from logs alone. ✅
- **III. Test-First**: `contracts/` enumerates every status code and `data-model.md` every state
  transition, giving a complete failing-test inventory before implementation. ✅
- **IV. Contract-Driven**: All three contract documents are additive; the one behavioural change to
  an existing endpoint (PUT/pause/resume now return 409 for a soft-deleted schedule) is only
  reachable for schedules that could not previously exist. ✅
- **V. YAGNI**: The design stayed at one boolean, three one-statement repository methods, and two
  exceptions; the alternatives that would have added a status enum, an audit trail, a purge job, or
  a generic filter framework are recorded as rejected in `research.md`. ✅

**Result**: PASS.

## Open Decision Carried Forward from the Spec

The spec's list-filter semantics (default `is_deleted=false`, two-valued filter, no "all" option)
remain a documented assumption rather than a stated requirement — see spec Assumptions and
`research.md` D5. The plan implements that assumption. Changing it later is a one-line change to the
query parameter's type/default plus the repository predicate, contained entirely within
`list_schedules` → `ScheduleService.list` → `ScheduleRepository.list`.

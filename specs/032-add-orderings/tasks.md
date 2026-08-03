# Tasks: Add Ordering for Schedules/Runs List

**Input**: Design documents from `specs/032-add-orderings/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED. The project constitution mandates Test-First (TDD) — every implementation
task is preceded by a failing test. Write the test, confirm it fails (Red), then implement (Green).

**Organization**: Tasks are grouped by user story. US1 (P1) builds the schedules ordering
machinery and is the MVP. US2 and US3 (both P2) extend that machinery by one sort field each and
therefore depend on US1. US4 (P3) is the independent runs-ordering slice.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US4 map to the user stories in spec.md
- Exact file paths are included in each task

## Path Conventions

Single web-service layout (existing): source under `src/api`, `src/lib`; tests under `tests/unit`
and `tests/integration` mirroring `src/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the working environment; no new dependencies are introduced by this feature.

- [X] T001 Confirm branch `032-add-orderings` is checked out and `uv sync` succeeds (no new deps; ordering uses stdlib SQLAlchemy `asc`/`desc`/`nulls_last` and `func.lower`).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared plumbing that every user story depends on: the direction enum and the
paginator link change that carries ordering across pages.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 [P] Add `SortDirection(StrEnum)` with `ASC = "asc"` / `DESC = "desc"` in src/lib/db/enums.py.
- [X] T003 [P] Write failing unit test that `Paginator.paginate` appends extra ordering query params (e.g. `order_by`, `order_dir`) to `next`/`previous` links in tests/unit/api/test_pagination.py.
- [X] T004 Extend `Paginator` in src/lib/schemas/pagination.py to accept an optional pre-formatted extra-query string (kept resource-agnostic) and append it to the generated `next`/`previous` URLs, making T003 pass (see research.md R6).

**Checkpoint**: Direction enum and pagination link support exist — user stories can begin.

---

## Phase 3: User Story 1 - Order schedules by creation date (Priority: P1) 🎯 MVP

**Goal**: `GET /v1/schedules` accepts `order_by=created_at` + `order_dir=asc|desc`, defaults to
`created_at desc` (unchanged behavior), rejects invalid values with 422, and stays stable across
pages via an `id` tiebreaker. This task group builds the reusable schedules-ordering machinery.

**Independent Test**: Request `?order_by=created_at&order_dir=asc` then `&order_dir=desc` and
confirm oldest→newest / newest→oldest; omit params and confirm `created_at desc`; send
`?order_by=bogus` and confirm HTTP 422.

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T005 [P] [US1] Write failing unit tests for the schedules ordering dependency (default = created_at/desc; valid values parsed; invalid `order_by`/`order_dir` → validation error) in tests/unit/api/dependencies/test_ordering.py.
- [X] T006 [P] [US1] Write failing unit tests for `ScheduleRepository.list` ordering by `created_at` asc/desc with `id` tiebreaker in tests/unit/lib/repositories/test_schedule_repository.py.
- [X] T007 [P] [US1] Write failing integration tests for `GET /v1/schedules?order_by=created_at&order_dir=asc|desc` ordering, default order, and invalid value → 422 in tests/integration/test_schedules_api.py.

### Implementation for User Story 1

- [X] T008 [US1] Add `ScheduleSortField(StrEnum)` with `CREATED_AT = "created_at"` in src/lib/db/enums.py (depends on T002).
- [X] T009 [US1] Add `ScheduleOrdering` Pydantic value object (`order_by: ScheduleSortField = CREATED_AT`, `order_dir: SortDirection = DESC`) in src/lib/schemas/ordering.py.
- [X] T010 [US1] Add the schedules ordering FastAPI dependency (typed `Query` params → `ScheduleOrdering`, mirroring `Pagination`) in src/api/dependencies/ordering.py.
- [X] T011 [US1] Parameterize `ScheduleRepository.list` in src/lib/repositories/schedule_repository.py to build `.order_by(...)` from the ordering value object for `created_at` (asc/desc) and always append `id` as tiebreaker; leave `.where(...)`/count query unchanged (data-model.md ordering table).
- [X] T012 [US1] Thread ordering through `ScheduleService.list` in src/lib/services/schedule_service.py (accept the ordering value object, forward to the repo, add `order_by`/`order_dir` to the existing structured log fields).
- [X] T013 [US1] Wire the ordering dependency into `list_schedules` in src/api/v1/endpoints/schedules.py, pass it to the service, and pass its formatted params to `paginator` so page links preserve order (T004).

**Checkpoint**: MVP — schedules can be ordered by creation date; the schedules-ordering machinery is reusable by US2/US3.

---

## Phase 4: User Story 2 - Order schedules by next run date (Priority: P2)

**Goal**: `order_by=next_run_time` (asc/desc) sorts on the outer-joined
`apscheduler_jobs.next_run_time`, with schedules that have no next run time (paused/inactive)
always placed last in both directions via `nulls_last()`.

**Independent Test**: With a mix of active schedules (varying next run times) and inactive
schedules (no next run), request `?order_by=next_run_time&order_dir=asc` and `desc` and confirm
active schedules ordered correctly and all NULL-next-run schedules appear last in both cases.

**Depends on**: US1 (extends `ScheduleSortField` and `ScheduleRepository.list`).

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T014 [P] [US2] Write failing unit tests for `ScheduleRepository.list` ordering by `next_run_time` asc/desc with `nulls_last` (NULL-next-run rows last in both directions) + `id` tiebreaker in tests/unit/lib/repositories/test_schedule_repository.py.
- [X] T015 [P] [US2] Write failing integration test (real DB) covering `next_run_time` ordering with a mix of joined/unjoined `apscheduler_jobs` rows so NULL next-run schedules sort last in both directions in tests/integration/test_schedule_repository.py.

### Implementation for User Story 2

- [X] T016 [US2] Add `NEXT_RUN_TIME = "next_run_time"` to `ScheduleSortField` in src/lib/db/enums.py.
- [X] T017 [US2] Extend the `.order_by(...)` builder in `ScheduleRepository.list` (src/lib/repositories/schedule_repository.py) with a `next_run_time` branch ordering on `apscheduler_jobs.c.next_run_time` using `.nulls_last()` + `id` tiebreaker (research.md R1).

**Checkpoint**: Schedules can be ordered by creation date AND next run date; inactive schedules always last.

---

## Phase 5: User Story 3 - Order schedules by name (Priority: P2)

**Goal**: `order_by=name` (asc/desc) sorts schedules alphabetically by `display_name`,
case-insensitively (`func.lower`), with `id` tiebreaker.

**Independent Test**: With schedules whose names include mixed-case values, request
`?order_by=name&order_dir=asc` and `desc` and confirm case-insensitive A→Z / Z→A ordering.

**Depends on**: US1 (extends `ScheduleSortField` and `ScheduleRepository.list`).

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [X] T018 [P] [US3] Write failing unit tests for `ScheduleRepository.list` ordering by `name` asc/desc, case-insensitive (`func.lower(display_name)`) + `id` tiebreaker in tests/unit/lib/repositories/test_schedule_repository.py.
- [X] T019 [P] [US3] Write failing integration test for `GET /v1/schedules?order_by=name&order_dir=asc|desc` confirming case-insensitive alphabetical order in tests/integration/test_schedules_api.py.

### Implementation for User Story 3

- [X] T020 [US3] Add `NAME = "name"` to `ScheduleSortField` in src/lib/db/enums.py.
- [X] T021 [US3] Extend the `.order_by(...)` builder in `ScheduleRepository.list` (src/lib/repositories/schedule_repository.py) with a `name` branch ordering on `func.lower(display_name)` + `id` tiebreaker (research.md R3a).

**Checkpoint**: All three schedule orderings (created_at, next_run_time, name) work.

---

## Phase 6: User Story 4 - Order runs by creation date (Priority: P3)

**Goal**: `GET /v1/schedules/{schedule_id}/runs` accepts `order_by=created_at` + `order_dir`,
defaults to `created_at desc` (unchanged), rejects invalid values (incl. `name`/`next_run_time`)
with 422, and stays stable across pages via `id` tiebreaker.

**Independent Test**: Request the runs list with `?order_dir=asc` and default, confirm
oldest→newest / newest→oldest; send `?order_by=name` and confirm HTTP 422; unknown/unowned
`schedule_id` still returns 404.

**Independent of** US1–US3 (separate endpoint, service, repository, and enums).

### Tests for User Story 4 (write first, confirm failing) ⚠️

- [X] T022 [P] [US4] Write failing unit tests for the runs ordering dependency (default = created_at/desc; `RunSortField` accepts only `created_at`; `next_run_time`/`name` → validation error) in tests/unit/api/dependencies/test_ordering.py.
- [X] T023 [P] [US4] Write failing unit tests for `ScheduleRunRepository.list_for_schedule` ordering by `created_at` asc/desc with `id` tiebreaker in tests/unit/lib/repositories/test_schedule_run_repository.py.
- [X] T024 [P] [US4] Write failing unit tests for `list_runs` endpoint ordering params (asc/desc, default, invalid → 422) in tests/unit/api/v1/endpoints/test_runs.py.

### Implementation for User Story 4

- [X] T025 [US4] Add `RunSortField(StrEnum)` with only `CREATED_AT = "created_at"` in src/lib/db/enums.py.
- [X] T026 [US4] Add `RunOrdering` Pydantic value object (`order_by: RunSortField = CREATED_AT`, `order_dir: SortDirection = DESC`) in src/lib/schemas/ordering.py.
- [X] T027 [US4] Add the runs ordering FastAPI dependency in src/api/dependencies/ordering.py.
- [X] T028 [US4] Parameterize `ScheduleRunRepository.list_for_schedule` in src/lib/repositories/schedule_run_repository.py to build `.order_by(...)` from the ordering value object for `created_at` (asc/desc) with `id` tiebreaker; leave `.where(...)`/count query unchanged.
- [X] T029 [US4] Thread ordering through `ScheduleRunService.list_runs` in src/lib/services/schedule_run_service.py (accept the ordering value object, forward to the repo, add order fields to logging).
- [X] T030 [US4] Wire the runs ordering dependency into `list_runs` in src/api/v1/endpoints/runs.py, pass it to the service, and pass its formatted params to `paginator` (ownership 404 check still precedes ordering).

**Checkpoint**: Runs can be ordered by creation date; all four user stories functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Verification and quality gates across all stories.

- [X] T031 [P] Verify/update Google-style docstrings on the two repository `.order_by` builders, the two service methods, and the ordering dependency; ensure no secrets/content are logged (constitution II).
- [X] T032 Run the manual + automated validation in specs/032-add-orderings/quickstart.md and confirm all acceptance outcomes (US1–US4, SC-001–SC-006).
- [X] T033 Run all quality gates and fix issues: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup. BLOCKS all user stories.
- **US1 (Phase 3)**: Depends on Foundational. MVP — builds shared schedules-ordering machinery.
- **US2 (Phase 4)**: Depends on US1 (extends `ScheduleSortField` + `ScheduleRepository.list`).
- **US3 (Phase 5)**: Depends on US1 (extends `ScheduleSortField` + `ScheduleRepository.list`).
- **US4 (Phase 6)**: Depends on Foundational only — independent of US1–US3 (separate endpoint/service/repo/enums).
- **Polish (Phase 7)**: Depends on all desired user stories being complete.

### Cross-story shared-file notes (avoid conflicts)

- `src/lib/db/enums.py` is edited by T002, T008, T016, T020, T025 — sequential, not parallel across stories.
- `src/lib/repositories/schedule_repository.py` is edited by T011, T017, T021 — sequential (same method).
- `src/lib/schemas/ordering.py` is edited by T009 and T026 — sequential.
- `src/api/dependencies/ordering.py` is edited by T010 and T027 — sequential.
- Test files `tests/unit/api/dependencies/test_ordering.py` (T005, T022) and `tests/unit/lib/repositories/test_schedule_repository.py` (T006, T014, T018) are appended across stories — coordinate if run in parallel.

### Within Each User Story

- Tests are written and MUST FAIL before implementation (constitution III).
- Enum/value-object → dependency → repository → service → endpoint.

### Parallel Opportunities

- Foundational: T002 and T003 are `[P]` (different files).
- Each story's test-writing tasks marked `[P]` can be authored together (distinct concerns), noting the shared-file caveat above.
- **US4 can run fully in parallel with US1/US2/US3** once Foundational is done — it touches an entirely separate set of files (runs endpoint/service/repo, `RunSortField`, `RunOrdering`).

---

## Parallel Example: after Foundational (Phase 2)

```bash
# Two independent tracks can proceed at once:
# Track A (schedules): US1 → US2 → US3 (US2/US3 extend US1's machinery)
# Track B (runs):      US4 (independent files)

# US1 test-writing tasks (write failing first):
Task: "Unit tests for schedules ordering dependency in tests/unit/api/dependencies/test_ordering.py"   # T005
Task: "Unit tests for ScheduleRepository.list created_at ordering in tests/unit/lib/repositories/test_schedule_repository.py"  # T006
Task: "Integration tests for GET /v1/schedules ordering in tests/integration/test_schedules_api.py"     # T007
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 (Setup) → Phase 2 (Foundational) → Phase 3 (US1).
2. STOP and VALIDATE: schedules order by creation date, default preserved, invalid → 422, stable pagination.
3. Deploy/demo if ready.

### Incremental Delivery

1. Foundational ready.
2. US1 (created_at) → validate → demo (MVP).
3. US2 (next_run_time, inactive-last) → validate → demo.
4. US3 (name, case-insensitive) → validate → demo.
5. US4 (runs created_at) → validate → demo. Can be delivered in parallel with US2/US3.

---

## Notes

- `[P]` = different files, no dependency on an incomplete task.
- `[Story]` label maps each task to a user story for traceability.
- Defaults reproduce current behavior (`created_at desc`), so the change is additive — no migration.
- Commit after each task or logical group; verify tests fail before implementing.

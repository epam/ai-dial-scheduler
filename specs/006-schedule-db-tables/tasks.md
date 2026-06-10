# Tasks: Schedule & Run Persistence Tables

**Input**: Design documents from `/specs/006-schedule-db-tables/`

**Prerequisites**: plan.md âœ…, spec.md âœ…, research.md âœ…, data-model.md âœ…, contracts/schema-contract.md âœ…, quickstart.md âœ…

**TDD Mandate (Constitution Â§ III â€” NON-NEGOTIABLE)**: Tests MUST be written and confirmed failing before implementation begins. The Red-Green-Refactor cycle is enforced in every story phase below.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify project structure and migration exclusion guardrails are in place before any implementation begins.

- [X] T001 Verify `tests/lib/models/` and `tests/integration/` directories exist (create `__init__.py` files if missing) per plan.md structure
- [X] T002 Verify `migrations/env.py` contains `_UNMANAGED_TABLES` that includes `apscheduler_jobs`, confirming autogenerate will never touch the engine table (FR-017, SC-005)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared enum needed by both the ScheduleRun model (US2) and downstream consumers. Must exist before any model can reference it.

**âš ï¸ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Create `ScheduleRunStatus(StrEnum)` with values `success`, `error`, `in_progress`, `missed` in `src/lib/db/enums.py` (R4, FR-010, schema-contract.md Â§ Status enumeration)

**Checkpoint**: Shared enum in place â€” user story implementation can now begin.

---

## Phase 3: User Story 1 â€” Query schedules with live next-run time (Priority: P1) ðŸŽ¯ MVP

**Goal**: Provide a queryable `schedules` table and a read-only `apscheduler_jobs` Core Table reference so any caller can retrieve schedule metadata joined to the engine's authoritative `next_run_time` via a single LEFT OUTER JOIN query.

**Independent Test**: Insert a `schedules` row whose `id` matches an `apscheduler_jobs` row with a non-null `next_run_time`, run the LEFT JOIN query, and assert the result contains both the schedule metadata and the engine's next-run time. Also verify rows are preserved when the engine row is absent or `next_run_time` is NULL.

### Tests for User Story 1 (TDD â€” write and confirm FAILING before T006)

> **Write these tests FIRST; all must FAIL before implementation starts.**

- [X] T004 [P] [US1] Write failing unit tests for `Schedule` model column types, `id` length 191, PK, and NOT NULL constraints in `tests/lib/models/test_schedule.py` (maps to FR-001â€“004, data-model.md Â§ Entity: Schedule)
- [X] T005 [P] [US1] Write failing integration tests for LEFT JOIN scenarios (active job with next_run_time, paused job with NULL next_run_time, schedule with no engine row) in `tests/integration/test_schedule_run_join.py` (maps to FR-005/006, SC-001, spec.md Â§ US1 acceptance scenarios 1â€“3)

### Implementation for User Story 1

- [X] T006 [US1] Implement read-only SQLAlchemy Core `Table` for `apscheduler_jobs` (columns: `id VARCHAR(191)`, `next_run_time DOUBLE PRECISION NULL`) on a **separate `MetaData`** (not `BaseSQLModel.metadata`) in `src/lib/models/apscheduler_job.py` (R2, FR-017, data-model.md Â§ External reference)
- [X] T007 [US1] Implement `Schedule` SQLModel extending `BaseSQLModel` with columns: `id String(191) PK`, `display_name String NOT NULL`, `created_by String NOT NULL`, `trigger JSONB NOT NULL`, `kwargs JSONB NOT NULL` in `src/lib/models/schedule.py` (R3/R5/R8, data-model.md Â§ Entity: Schedule, schema-contract.md Â§ Table `schedules`)
- [X] T008 [US1] Update `src/lib/models/__init__.py` to export `Schedule` and `apscheduler_jobs` (the Core Table reference)

**Checkpoint**: `Schedule` model and `apscheduler_jobs` reference in place. Run `tests/lib/models/test_schedule.py` and `tests/integration/test_schedule_run_join.py` â€” US1 scenarios must now PASS.

---

## Phase 4: User Story 2 â€” Persist a complete history of schedule runs (Priority: P2)

**Goal**: Provide a `schedule_runs` table that records every execution attempt of a schedule, with status, timing, result payload, and a point-in-time snapshot of the schedule definition at run time â€” so run history stays accurate even after the parent schedule is edited.

**Independent Test**: Create a `Schedule`, insert run records for all four statuses (including `in_progress` with no `end_time`), read them back, and verify every field (status, timing, result, snapshot) is preserved exactly. Then edit the parent schedule and confirm existing run snapshots are unchanged.

### Tests for User Story 2 (TDD â€” write and confirm FAILING before T011)

> **Write these tests FIRST; all must FAIL before implementation starts.**

- [X] T009 [P] [US2] Write failing unit tests for `ScheduleRun` model: UUID PK, `end_time` nullable, `result` nullable, `schedule_id VARCHAR(191) NOT NULL`, `schedule_snapshot JSONB NOT NULL`, and DB CHECK constraint rejects an out-of-set status value in `tests/lib/models/test_schedule_run.py` (maps to FR-007â€“012, R4/R5/R6, data-model.md Â§ Entity: ScheduleRun)

### Implementation for User Story 2

- [X] T010 [US2] Implement `ScheduleRun` SQLModel extending `BaseSQLModel` with columns: `id UUID PK (default=uuid4)`, `end_time DateTime(timezone=True) NULL`, `status String NOT NULL`, `result JSONB NULL`, `schedule_id String(191) NOT NULL FKâ†’schedules.id ON DELETE RESTRICT (indexed)`, `schedule_snapshot JSONB NOT NULL`; add `__table_args__` with named CHECK `CK_schedule_runs_status` and index `IX_schedule_runs_schedule_id` and FK `FK_schedule_runs_schedule_id_schedules` in `src/lib/models/schedule_run.py` (R4/R5/R6/R7, data-model.md Â§ Entity: ScheduleRun, schema-contract.md Â§ Table `schedule_runs`)
- [X] T011 [US2] Update `src/lib/models/__init__.py` to export `ScheduleRun`

**Checkpoint**: `ScheduleRun` model in place. Run `tests/lib/models/test_schedule_run.py` â€” all US2 scenarios must PASS.

---

## Phase 5: User Story 3 â€” Protect run history integrity against schedule deletion (Priority: P3)

**Goal**: The `ON DELETE RESTRICT` FK from `schedule_runs.schedule_id` to `schedules.id` blocks any deletion of a schedule that still has runs, preserving the audit trail. A schedule with no runs can be deleted freely.

**Independent Test**: Create a `Schedule`, attach one `ScheduleRun` to it, attempt `DELETE FROM schedules`, and assert the database rejects it while both rows remain intact. Then delete all runs and confirm the schedule can now be deleted.

### Tests for User Story 3 (TDD â€” write and confirm FAILING before confirming Phase 4 fully satisfies US3)

> **These tests exercise the RESTRICT FK defined in T010. Write them now; they must PASS once T010 is in place.**

- [X] T012 [US3] Write integration tests for delete protection: (a) schedule with runs â†’ delete rejected, both rows intact; (b) schedule with no runs â†’ delete succeeds in `tests/integration/test_schedule_run_join.py` (maps to FR-013/014, SC-004, spec.md Â§ US3 acceptance scenarios 1â€“2, R7)

**Checkpoint**: All three user stories are independently functional and tested. Run full test suite.

---

## Phase 6: Polish & Migration

**Purpose**: Generate the Alembic migration from the finalized models, run all quality gates, and validate full upgrade/downgrade reversibility.

- [ ] T013 ⚠️ NEEDS-DB Generate Alembic autogenerated migration: `uv run alembic revision --autogenerate -m "create schedules and schedule_runs tables"` â€” verify output contains `create_table("schedules", ...)`, `create_table("schedule_runs", ...)`, `CK_schedule_runs_status` CHECK, `FK_schedule_runs_schedule_id_schedules` FK with `ondelete='RESTRICT'`, `IX_schedule_runs_schedule_id` index, and **no** reference to `apscheduler_jobs` (FR-016/017, SC-005, quickstart.md Â§ 1)
- [X] T014 Run `uv run pytest` and fix any remaining failing tests
- [X] T015 [P] Run `uv run ruff check src tests` and `uv run ruff format src tests`, fix all reported issues
- [X] T016 [P] Run `uv run mypy src`, fix all type errors
- [ ] T017 ⚠️ NEEDS-DB Verify migration reversibility: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` â€” all three commands must succeed with no orphaned objects (FR-018, SC-005, quickstart.md Â§ 2)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies â€” can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1
- **User Story 1 (Phase 3)**: Depends on Phase 2 (needs enum in place) â€” MVP
- **User Story 2 (Phase 4)**: Depends on Phase 3 (ScheduleRun FK â†’ schedules.id requires Schedule model)
- **User Story 3 (Phase 5)**: Depends on Phase 4 (RESTRICT FK implemented in ScheduleRun)
- **Polish & Migration (Phase 6)**: Depends on all user stories complete

### User Story Dependencies

- **US1 (P1)**: Depends only on Foundational (Phase 2) â€” Schedule model + apscheduler_job ref are independent
- **US2 (P2)**: Depends on US1 â€” ScheduleRun.schedule_id FK references schedules.id
- **US3 (P3)**: Depends on US2 â€” RESTRICT FK is part of ScheduleRun model definition

### Within Each User Story

- Tests MUST be written and confirmed FAILING before implementation (Constitution Â§ III)
- Models before exports (`__init__.py` updates)
- Core implementation before migration generation

### Parallel Opportunities

- T004 and T005 (US1 tests) can run in parallel â€” different test files
- T009 (US2 tests) can run in parallel with later T010/T011 prep
- T015 and T016 (ruff + mypy) can run in parallel in Phase 6

---

## Parallel Example: User Story 1 Tests

```bash
# Write both test files simultaneously (different files):
Task: T004 â€” "Write failing tests for Schedule model in tests/lib/models/test_schedule.py"
Task: T005 â€” "Write failing integration tests for LEFT JOIN in tests/integration/test_schedule_run_join.py"

# Then implement:
Task: T006 â€” apscheduler_job.py  (T006 and T007 touch different files â†’ can be parallel)
Task: T007 â€” schedule.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (shared enum)
3. Complete Phase 3: User Story 1 (Schedule model + join ref + tests)
4. **STOP and VALIDATE**: Run `uv run pytest tests/lib/models/test_schedule.py tests/integration/test_schedule_run_join.py`

### Incremental Delivery

1. Setup + Foundational â†’ Foundation ready
2. US1 â†’ `schedules` table + join reference (MVP â€” proves core queryable-schedule capability)
3. US2 â†’ `schedule_runs` table (adds run persistence)
4. US3 â†’ delete-protection test validation (hardens US2 model)
5. Phase 6 â†’ migration generated, quality gates green

---

## Notes

- [P] tasks operate on different files with no blocking inter-task dependencies
- All model tests are unit tests (inspect `__table_args__`, column types via SQLAlchemy introspection â€” no live DB required)
- Integration tests (`tests/integration/`) require a live PostgreSQL instance and apply the migration before running
- The `apscheduler_jobs` Core Table uses a **separate `MetaData`** â€” never pass it to `BaseSQLModel.metadata` (R2)
- Do NOT edit the generated migration file (FR-016); if the output is wrong, fix the model and regenerate
- `status` is a `String` column with a named CHECK â€” NOT a native PG enum (R4 decision is final; native enum breaks reversibility)

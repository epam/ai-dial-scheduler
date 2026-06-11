---
description: "Task list for Schedule Management Endpoints"
---

# Tasks: Schedule Management Endpoints

**Input**: Design documents from `/specs/007-schedule-endpoints/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/schedule-endpoints.md, quickstart.md

**Tests**: INCLUDED — the constitution's Project SDLC mandates a TDD approach (unit tests written before implementation). Test tasks therefore appear first within each phase and MUST fail before the corresponding implementation task is done.

**Organization**: Tasks are grouped by user story (US1 Create, US2 Update, US3 List, US4 Details). Note: the repository, service, and router are single shared files touched by multiple stories, so cross-story tasks on those files are sequential, not parallel — see Dependencies.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1/US2/US3/US4 (setup, foundational, polish have no story label)
- Exact file paths are included in every task

## Path Conventions

Web-service layout (constitution): `src/api/` (entrypoint), `src/worker/` (executor), `src/lib/` (shared library), tests mirror `src/` under `tests/unit/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Test package structure and prerequisite verification

- [X] T001 [P] Create test package directories with empty `__init__.py`: `tests/unit/lib/schemas/`, `tests/unit/lib/repositories/`, `tests/unit/lib/services/`, `tests/unit/api/`, `tests/unit/api/v1/`, `tests/unit/api/v1/endpoints/`
- [X] T002 [P] Verify no migration is required: confirm `schedules` and `apscheduler_jobs` exist (feature 006) and that this feature introduces no new ORM table; record the no-op in `specs/007-schedule-endpoints/research.md` (R10) if anything diverges

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared types, schemas, helpers, repository/service scaffolds, DI, and router wiring that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests for Foundational (write first — must fail)

- [X] T003 [P] Schema validation tests in `tests/unit/lib/schemas/test_schedule_schemas.py`: trigger exactly-one (zero/both → error), `CronFields` all-optional pass-through, `ScheduleProperties` target-type matrix (chat_completion url + api_version + stream default; rest/sse require method) per data-model.md
- [X] T004 [P] `build_trigger` tests in `tests/unit/lib/services/test_trigger_factory.py`: cron spec → `CronTrigger` (UTC, start/end dates), date → `DateTrigger` (UTC)
- [X] T005 [P] Worker job test in `tests/unit/worker/test_jobs.py`: `execute_scheduled_job` logs current UTC datetime and its kwargs (assert via captured logger), accepts `schedule_id` plus property fields

### Implementation for Foundational

- [X] T006 [P] Add constants `MOCK_USER_ID = "mock_user_id"`, `EXECUTE_SCHEDULED_JOB_REF = "src.worker.jobs:execute_scheduled_job"`, `DEFAULT_PAGE_LIMIT = 20` in `src/lib/constants.py`
- [X] T007 [P] Add `TargetType`, `HttpMethod`, `TriggerType` StrEnums in `src/lib/db/enums.py` (values per data-model.md)
- [X] T008 [P] Add `ScheduleNotFoundError` and `JobRegistrationError` exceptions in `src/lib/exceptions/__init__.py`
- [X] T009 Implement trigger schemas (`CronFields`, `CronTriggerSpec`, `ScheduleTrigger` with exactly-one `model_validator` and `trigger_type` derivation) in `src/lib/schemas/schedule.py` (depends on T007)
- [X] T010 Implement `ScheduleProperties` with target-type `model_validator` in `src/lib/schemas/schedule.py` (same file, after T009; depends on T007)
- [X] T011 Implement `ScheduleRequest`, `ScheduleResponse`, `ScheduleListItem`, `PaginatedSchedules` in `src/lib/schemas/schedule.py` (same file, after T010)
- [X] T012 [P] Implement `build_trigger(trigger: ScheduleTrigger) -> CronTrigger | DateTrigger` (UTC) in `src/lib/services/trigger_factory.py` (depends on T009)
- [X] T013 [P] Implement placeholder `execute_scheduled_job(schedule_id: str, **properties)` (logs UTC now + kwargs via `logger`) in `src/worker/jobs.py`
- [X] T014 Create `ScheduleRepository` scaffold in `src/lib/repositories/schedule_repository.py`: constructor takes `AsyncSession`; private `_to_next_run_time(epoch: float | None) -> datetime | None` (epoch→aware UTC); private select helper joining `apscheduler_jobs` (Core `Table` from `src/lib/models/apscheduler_job.py`)
- [X] T015 Create `ScheduleService` scaffold in `src/lib/services/schedule_service.py`: constructor takes `ScheduleRepository` + `AsyncIOScheduler`; private `_register_job(schedule_id, trigger, properties)` calling `await asyncio.to_thread(scheduler.add_job, ...)` (id, `replace_existing=True`, `func=EXECUTE_SCHEDULED_JOB_REF`, kwargs = properties ∪ `{schedule_id}`); private `_to_response(schedule, next_run_time)` (depends on T011, T012, T014)
- [X] T016 [P] Implement DI providers `get_scheduler(request)` and `get_schedule_service(...)` in `src/api/dependencies/scheduler.py` (wires `get_db_session` + scheduler from `request.app.state.scheduler`)
- [X] T017 Create `/schedules` router skeleton (prefix, trailing-slash collection paths, `tags`) in `src/api/v1/endpoints/schedules.py` and register it in `src/api/app.py` `create_api()` (depends on T016)

**Checkpoint**: Shared types, helpers, scaffolds, DI, and routing exist — user stories can now be implemented.

---

## Phase 3: User Story 1 - Create a schedule (Priority: P1) 🎯 MVP

**Goal**: Persist a validated schedule, register it with the scheduler, and return it with `next_run_time`; roll back the row if registration fails.

**Independent Test**: POST a valid cron or date schedule → `201` with uuid4 `id`, `created_by="mock_user_id"`, non-null `next_run_time`; both/zero triggers or invalid properties → `422` with nothing persisted; forced `add_job` failure → `502` with no orphaned row.

### Tests for User Story 1 (write first — must fail)

- [X] T018 [P] [US1] Repository create/delete test in `tests/unit/lib/repositories/test_schedule_repository.py`: `create` inserts+commits; `delete` removes the row
- [X] T019 [P] [US1] Service create test in `tests/unit/lib/services/test_schedule_service.py`: success path (uuid generated, row persisted, `_register_job` called, `next_run_time` read back) and **saga rollback** (mock scheduler `add_job` raising → row deleted, `JobRegistrationError` raised)
- [X] T020 [P] [US1] Endpoint test in `tests/unit/api/v1/endpoints/test_schedules.py`: `POST /schedules/` → 201 body shape; invalid trigger/properties → 422; registration failure → 502

### Implementation for User Story 1

- [X] T021 [US1] Implement `create(schedule)` and `delete(schedule_id)` in `src/lib/repositories/schedule_repository.py`
- [X] T022 [US1] Implement `ScheduleService.create(request, created_by)` in `src/lib/services/schedule_service.py`: `id=str(uuid4())`, persist via repo, `_register_job`, on failure `repo.delete` + raise `JobRegistrationError`, read `next_run_time`, return `_to_response`; log enqueue/rollback with `schedule_id` + reason (depends on T021)
- [X] T023 [US1] Implement `POST /schedules/` handler (status 201) in `src/api/v1/endpoints/schedules.py` delegating to the service; map `JobRegistrationError`→502 (depends on T022)

**Checkpoint**: Create works end-to-end and is independently testable (MVP).

---

## Phase 4: User Story 2 - Update a schedule (Priority: P2)

**Goal**: Update an owned schedule and re-register it; restore the prior row state if re-registration fails.

**Independent Test**: PUT a valid change to an existing owned id → `200` with recomputed `next_run_time` and newer `updated_at`; unknown/non-owned id → `404`; invalid body → `422` (row unchanged); forced `add_job` failure → `502` with prior values restored.

### Tests for User Story 2 (write first — must fail)

- [X] T024 [P] [US2] Repository update test in `tests/unit/lib/repositories/test_schedule_repository.py`: `update` of owned row commits new values; returns `None` for missing/non-owned id
- [X] T025 [P] [US2] Service update test in `tests/unit/lib/services/test_schedule_service.py`: success path and **restore-on-failure** (mock `add_job` raising → prior `display_name`/`trigger`/`kwargs` restored, `JobRegistrationError` raised); missing id → `ScheduleNotFoundError`
- [X] T026 [P] [US2] Endpoint test in `tests/unit/api/v1/endpoints/test_schedules.py`: `PUT /schedules/{id}` → 200; unknown id → 404; invalid body → 422; registration failure → 502

### Implementation for User Story 2

- [X] T027 [US2] Implement `update(schedule_id, created_by, *, display_name, trigger, kwargs)` (scoped, returns `Schedule | None`) in `src/lib/repositories/schedule_repository.py`
- [X] T028 [US2] Implement `ScheduleService.update(schedule_id, request, created_by)` in `src/lib/services/schedule_service.py`: load owned row (else `ScheduleNotFoundError`), capture prior values, update+commit, `_register_job(replace_existing)`, on failure restore prior values + raise; read `next_run_time`, return `_to_response` (depends on T027)
- [X] T029 [US2] Implement `PUT /schedules/{schedule_id}` handler in `src/api/v1/endpoints/schedules.py`; map `ScheduleNotFoundError`→404, `JobRegistrationError`→502 (depends on T028)

**Checkpoint**: Create + Update both work independently.

---

## Phase 5: User Story 3 - List schedules (Priority: P2)

**Goal**: Return a paginated, owner-scoped list of schedule summaries with next-run times and nav links.

**Independent Test**: With several owned schedules, `GET /schedules/?limit=&offset=` → `200` with accurate `count`, page of summaries (`trigger_type` cron/date), `previous` null on first page, `next` null on last page; other users' schedules excluded.

### Tests for User Story 3 (write first — must fail)

- [X] T030 [P] [US3] Repository list test in `tests/unit/lib/repositories/test_schedule_repository.py`: pagination (limit/offset), `created_by` scoping, total count, LEFT JOIN yields `next_run_time` (and `None` when no job)
- [X] T031 [P] [US3] Service list test in `tests/unit/lib/services/test_schedule_service.py`: builds `PaginatedSchedules`, derives `trigger_type`, computes `next`/`previous` link presence at boundaries
- [X] T032 [P] [US3] Endpoint test in `tests/unit/api/v1/endpoints/test_schedules.py`: `GET /schedules/` returns paginated body; defaults `limit=20`, `offset=0`

### Implementation for User Story 3

- [X] T033 [US3] Implement `list(created_by, limit, offset)` (count + page, scoped, LEFT JOIN `apscheduler_jobs`) in `src/lib/repositories/schedule_repository.py`
- [X] T034 [US3] Implement `ScheduleService.list(created_by, limit, offset)` in `src/lib/services/schedule_service.py`: map rows → `ScheduleListItem`, build `next`/`previous` query strings, assemble `PaginatedSchedules` (depends on T033)
- [X] T035 [US3] Implement `GET /schedules/` handler with `limit`/`offset` query params (defaults from `DEFAULT_PAGE_LIMIT`) in `src/api/v1/endpoints/schedules.py` (depends on T034)

**Checkpoint**: Create + Update + List all work independently.

---

## Phase 6: User Story 4 - Schedule details (Priority: P2)

**Goal**: Return the full detail object for an owned schedule, or 404.

**Independent Test**: `GET /schedules/{id}` for an owned id → `200` full object (trigger, properties, next_run_time, timestamps); missing/non-owned id → `404`.

### Tests for User Story 4 (write first — must fail)

- [X] T036 [P] [US4] Repository get test in `tests/unit/lib/repositories/test_schedule_repository.py`: `get` returns owned row + joined `next_run_time`; `None` for missing/non-owned id
- [X] T037 [P] [US4] Service get test in `tests/unit/lib/services/test_schedule_service.py`: returns `ScheduleResponse`; raises `ScheduleNotFoundError` when absent
- [X] T038 [P] [US4] Endpoint test in `tests/unit/api/v1/endpoints/test_schedules.py`: `GET /schedules/{id}` → 200 full body; unknown id → 404

### Implementation for User Story 4

- [X] T039 [US4] Implement `get(schedule_id, created_by)` (owned row + LEFT JOIN `next_run_time`, `None` if not owned) in `src/lib/repositories/schedule_repository.py`
- [X] T040 [US4] Implement `ScheduleService.get(schedule_id, created_by)` in `src/lib/services/schedule_service.py` (raise `ScheduleNotFoundError` if absent) (depends on T039)
- [X] T041 [US4] Implement `GET /schedules/{schedule_id}` handler in `src/api/v1/endpoints/schedules.py`; map `ScheduleNotFoundError`→404 (depends on T040)

**Checkpoint**: All four endpoints independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates and consistency across all stories

- [X] T042 [P] Verify structured observability on every scheduling decision (create/enqueue, update, rollback/compensation) in `src/lib/services/schedule_service.py`: log includes `schedule_id`, decision type, reason, ISO-8601 UTC (Principle II)
- [X] T043 [P] Add Google-style docstrings and remove any non-essential comments across new modules (`schedule.py`, `schedule_repository.py`, `schedule_service.py`, `trigger_factory.py`, `schedules.py`, `scheduler.py` deps)
- [X] T044 Run `uv run ruff check src tests` and `uv run ruff format --check src tests`; fix violations
- [X] T045 Run `uv run mypy src` and fix type issues; then `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml` and ensure new code is covered and green
- [X] T046 Execute `specs/007-schedule-endpoints/quickstart.md` scenarios 1–5 against running API + worker to confirm end-to-end behavior

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — start immediately
- **Foundational (Phase 2)**: depends on Setup — BLOCKS all user stories
- **User Stories (Phase 3–6)**: all depend on Foundational completion
- **Polish (Phase 7)**: depends on the desired stories being complete

### User Story Dependencies

- **US1 (P1)**: after Foundational — no dependency on other stories (MVP)
- **US2 (P2)**: after Foundational — independent of US1 (but reuses `_register_job`)
- **US3 (P2)**: after Foundational — independent
- **US4 (P2)**: after Foundational — independent

### Shared-file note (important)

`src/lib/repositories/schedule_repository.py`, `src/lib/services/schedule_service.py`, and
`src/api/v1/endpoints/schedules.py` each receive method/handler additions from **every** story.
The shared scaffolds are built once in Foundational (T014, T015, T017); each story then appends
its own method/handler. Implementation tasks across different stories that touch the **same
file** must run sequentially (no `[P]` across stories on those files), but each story remains
independently testable once its methods land.

### Within Each User Story

- Tests (T0xx) written first and must fail → then implementation
- Repository method → Service method → Endpoint handler (strict order)

---

## Parallel Opportunities

- **Setup**: T001, T002 in parallel
- **Foundational tests**: T003, T004, T005 in parallel (different files)
- **Foundational impl**: T006, T007, T008 in parallel; T012, T013, T016 in parallel after their deps; T009→T010→T011 sequential (same file)
- **Within a story**: the three test tasks (`[P]`) run in parallel (different files); implementation is sequential (repo→service→endpoint)
- **Polish**: T042, T043 in parallel

### Parallel Example: Foundational tests

```bash
Task: "Schema validation tests in tests/unit/lib/schemas/test_schedule_schemas.py"   # T003
Task: "build_trigger tests in tests/unit/lib/services/test_trigger_factory.py"        # T004
Task: "Worker job test in tests/unit/worker/test_jobs.py"                              # T005
```

### Parallel Example: User Story 1 tests

```bash
Task: "Repository create/delete test in tests/unit/lib/repositories/test_schedule_repository.py"  # T018
Task: "Service create + rollback test in tests/unit/lib/services/test_schedule_service.py"        # T019
Task: "Endpoint POST test in tests/unit/api/v1/endpoints/test_schedules.py"                       # T020
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1: Setup
2. Phase 2: Foundational (CRITICAL — blocks all stories)
3. Phase 3: User Story 1 (Create)
4. **STOP and VALIDATE**: POST a schedule, confirm 201 + worker executes the placeholder job
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 Create → test → demo (MVP)
3. US2 Update → test → demo
4. US3 List → test → demo
5. US4 Details → test → demo
6. Polish → quality gates green

---

## Notes

- `[P]` = different files, no incomplete dependencies
- TDD per constitution: every test task precedes its implementation and must fail first
- The service registers the worker job by **string reference** (`EXECUTE_SCHEDULED_JOB_REF`) — never import `src.worker` from `src.lib` (layer rule)
- `add_job` runs via `asyncio.to_thread` (no event-loop blocking); cross-store consistency uses the compensating-transaction (saga) pattern from research R3
- `next_run_time` always comes from `apscheduler_jobs` (epoch→UTC), never recomputed
- Commit after each task or logical group; stop at any checkpoint to validate a story independently

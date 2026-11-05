---

description: "Task list for Schedule Run List & Run Details Endpoints"
---

# Tasks: Schedule Run List & Run Details Endpoints

**Input**: Design documents from `/specs/010-schedule-run-endpoints/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅

**Tests**: TDD approach — unit tests written and failing before implementation per constitution §III.

**Organization**: Tasks grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add shared exception and response schemas needed by all user stories.

- [X] T001 [P] Add `ScheduleRunNotFoundError` to `src/lib/exceptions/errors.py`
- [X] T002 [P] Add `RunListItem`, `RunScheduleSummary`, `RunResponse` schemas to `src/lib/schemas/run.py`

**Checkpoint**: Exception + schemas ready — repository, service, and endpoint work can begin.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data access and service methods that all user stories depend on.

**⚠️ CRITICAL**: No user story endpoint work can begin until this phase is complete.

- [X] T003 [P] Write failing unit tests for `list_for_schedule` and `get_for_schedule` in `tests/unit/lib/repositories/test_schedule_run_repository.py`
- [X] T004 Add `list_for_schedule(schedule_id, limit, offset)` to `src/lib/repositories/schedule_run_repository.py` — ordered `created_at DESC, id DESC`, returns `(total_count, list[ScheduleRun])`
- [X] T005 Add `get_for_schedule(schedule_id, run_id)` to `src/lib/repositories/schedule_run_repository.py` — fetches by PK, returns `None` if `schedule_id` does not match
- [X] T006 [P] Write failing unit tests for `ScheduleRunService.list_runs` and `ScheduleRunService.get_run` in `tests/unit/lib/services/test_schedule_run_service.py`
- [X] T007 Create `src/lib/services/schedule_run_service.py` with `ScheduleRunService`: `list_runs(schedule_id, created_by, pagination)` calls `ScheduleRepository.get` first (raises `ScheduleNotFoundError` when `None`), then `list_for_schedule`; `get_run(schedule_id, created_by, run_id)` calls `ScheduleRepository.get` first, then `get_for_schedule` (raises `ScheduleRunNotFoundError` when `None`)
- [X] T008 Create `src/api/dependencies/runs.py` with `get_schedule_run_service` FastAPI dependency constructing `ScheduleRunService` from an `AsyncSession`

**Checkpoint**: Foundation ready — endpoint implementation for all stories can now begin in parallel.

---

## Phase 3: User Story 1 — Browse Run History (Priority: P1) 🎯 MVP

**Goal**: Owner can list all runs for their schedule, paginated, with status and timing.

**Independent Test**: Seed a schedule with 3+ runs, call `GET /v1/schedules/{id}/runs`, verify paginated response with total count, run ids/statuses/times, next/previous links.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T009 [P] [US1] Write failing unit tests for `list_runs` endpoint handler in `tests/unit/api/v1/endpoints/test_runs.py`: 200 with page, 200 empty list (never ran), pagination links present/absent
- [X] T010 [P] [US1] Write failing integration test scenarios for `GET /v1/schedules/{id}/runs` in `tests/integration/test_runs_api.py`: returns runs in `created_at DESC` order, empty schedule returns 200+count=0, second page navigates correctly

### Implementation for User Story 1

- [X] T011 [US1] Implement `list_runs` handler in `src/api/v1/endpoints/runs.py` — `GET /` on `APIRouter(prefix="/schedules/{schedule_id}/runs", tags=["runs"])`, uses `get_schedule_run_service` + `Pagination` dep, calls `service.list_runs`, returns `Page[RunListItem]`; maps `ScheduleRun.created_at` → `start_time`
- [X] T012 [US1] Register `runs_router` in `src/api/app.py` with `prefix="/v1"`

**Checkpoint**: `GET /v1/schedules/{id}/runs` fully functional and independently testable.

---

## Phase 4: User Story 2 — Inspect a Single Run (Priority: P1)

**Goal**: Owner can fetch full details of a specific run including embedded schedule summary.

**Independent Test**: For a known run ID under an owned schedule, call `GET /v1/schedules/{id}/runs/{run_id}`, verify status, start/end times, result, and embedded `schedule.display_name`/`trigger`/`properties`.

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T013 [P] [US2] Write failing unit tests for `get_run` endpoint handler in `tests/unit/api/v1/endpoints/test_runs.py`: 200 with full `RunResponse`, 404 when run not found, embedded schedule fields present
- [X] T014 [P] [US2] Add failing integration test scenarios for `GET /v1/schedules/{id}/runs/{run_id}` in `tests/integration/test_runs_api.py`: successful run returns result, failed run returns error_text, in-progress run returns null end_time, wrong run_id returns 404

### Implementation for User Story 2

- [X] T015 [US2] Implement `get_run` handler in `src/api/v1/endpoints/runs.py` — `GET /{run_id}` with `run_id: uuid.UUID` path param, uses `get_schedule_run_service`, calls `service.get_run(schedule_id, created_by, str(run_id))`, returns `RunResponse` with embedded `RunScheduleSummary` built from live `Schedule` row; catches `ScheduleRunNotFoundError` → 404

**Checkpoint**: `GET /v1/schedules/{id}/runs/{run_id}` fully functional and independently testable.

---

## Phase 5: User Story 3 — Ownership Enforcement (Priority: P1)

**Goal**: Cross-owner and non-existent schedule/run requests return 404 indistinguishable from genuine not-found.

**Independent Test**: As user A, request run list and run detail for user B's schedule ID; both return 404 with the same body as requesting a schedule that never existed.

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T016 [P] [US3] Write failing unit tests for ownership rejection in `tests/unit/api/v1/endpoints/test_runs.py`: list returns 404 when `ScheduleNotFoundError` raised, detail returns 404 when `ScheduleNotFoundError` raised
- [X] T017 [P] [US3] Add failing integration test scenarios for ownership in `tests/integration/test_runs_api.py`: user B's schedule_id → 404 for list; user B's schedule_id → 404 for detail; non-existent schedule_id → 404 identical to cross-owner case; run belonging to different schedule → 404

### Implementation for User Story 3

- [X] T018 [US3] Ensure both handlers catch `ScheduleNotFoundError` → HTTP 404 in `src/api/v1/endpoints/runs.py` (verify the existing exception handler in `src/api/app.py` covers it, or add explicit mapping)
- [X] T019 [US3] Verify `get_run` catches `ScheduleRunNotFoundError` → HTTP 404 in `src/api/v1/endpoints/runs.py`

**Checkpoint**: All three user stories independently functional. Ownership is opaque to callers.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates, observability, and validation across all stories.

- [X] T020 [P] Add structured log calls in `src/api/v1/endpoints/runs.py` and `src/lib/services/schedule_run_service.py` — log `schedule_id`, `created_by` (no result payloads or secrets) per constitution §II
- [X] T021 [P] Run `uv run ruff check src tests --fix` and `uv run ruff format src tests` — fix any remaining lint/format issues
- [X] T022 [P] Run `uv run mypy src` — fix all type errors
- [X] T023 Run `uv run pytest --cov=src` — confirm all tests pass, verify new code coverage
- [ ] T024 Run quickstart.md scenarios manually against a local stack to verify the golden path end-to-end

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately; T001 and T002 are parallel
- **Foundational (Phase 2)**: Depends on Phase 1 (T001, T002) — BLOCKS all endpoint phases
  - T003 (write tests) can start as soon as Phase 1 is done
  - T004 and T005 are sequential (same file, depend on T003)
  - T006 (write service tests) can run in parallel with T004/T005
  - T007 depends on T004 + T005 + T006
  - T008 depends on T007
- **User Stories (Phase 3–5)**: All depend on Foundational phase completion (T008)
  - US1 (Phase 3) and US2 (Phase 4) can proceed in parallel after Phase 2
  - US3 (Phase 5) verifies behavior established by US1+US2 — can be written at any time but integration tests need Phase 3+4 handlers in place
- **Polish (Phase 6)**: Depends on all user story phases complete

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational — No dependency on US2/US3
- **US2 (P1)**: Can start after Foundational — No dependency on US1 (shares the same router file; coordinate if working in parallel)
- **US3 (P1)**: Can start after Foundational — verifies the ownership path already provided by US1+US2 handlers; T018/T019 are small verification tasks

### Within Each User Story

- Write tests (marked [P]) and confirm they FAIL before writing implementation
- Models/schemas (Phase 1) before services (Phase 2) before endpoints (Phase 3+)
- Each story complete and tests passing before moving to next

### Parallel Opportunities

- T001 and T002 (Phase 1 setup) run in parallel
- T003 and T006 (test stubs) run in parallel
- T009 and T010 (US1 test stubs) run in parallel
- T013 and T014 (US2 test stubs) run in parallel
- T016 and T017 (US3 test stubs) run in parallel
- T020, T021, T022 (polish) run in parallel

---

## Parallel Example: User Story 1

```bash
# Write tests first (both parallel):
Task T009: "Failing unit tests for list_runs handler in tests/unit/api/v1/endpoints/test_runs.py"
Task T010: "Failing integration tests for GET /v1/schedules/{id}/runs in tests/integration/test_runs_api.py"

# Then implement:
Task T011: "list_runs handler in src/api/v1/endpoints/runs.py"
Task T012: "Register runs_router in src/api/app.py"
```

---

## Implementation Strategy

### MVP First (All Three Stories Together)

All three user stories are P1 and tightly related — US3 is a verification layer over US1+US2. The MVP is the complete feature:

1. Complete Phase 1: Setup (T001–T002)
2. Complete Phase 2: Foundational (T003–T008)
3. Complete Phase 3: US1 list endpoint (T009–T012)
4. Complete Phase 4: US2 detail endpoint (T013–T015)
5. Complete Phase 5: US3 ownership verification (T016–T019)
6. **STOP and VALIDATE**: Run full test suite, verify quickstart scenarios
7. Complete Phase 6: Polish (T020–T024)

### Incremental Delivery

1. Phase 1+2 → Foundation ready (schemas, repo methods, service, dependency)
2. Phase 3 → List endpoint live (US1 independently testable)
3. Phase 4 → Detail endpoint live (US2 independently testable)
4. Phase 5 → Ownership enforced (US3 verified end-to-end)
5. Phase 6 → CI gates green, observability complete

---

## Notes

- [P] tasks = different files, no blocking dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Verify tests FAIL before implementing
- Commit after each task or logical group
- `run_id` path param must be typed `uuid.UUID`; pass `str(run_id)` into service (D5)
- `start_time` in responses maps to `ScheduleRun.created_at` — not a column named `start_time` (D1)
- Embedded `schedule` in `RunResponse` comes from the live `Schedule` row, not `schedule_snapshot` (D2)
- Ordering: `created_at DESC, id DESC` for stable paging (D7)
- `created_by` uses `MOCK_USER_ID` from `src/lib/constants.py` for now (D9)

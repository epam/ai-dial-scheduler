---

description: "Task list for Schedule Pause & Resume Endpoints"
---

# Tasks: Schedule Pause & Resume Endpoints

**Input**: Design documents from `specs/012-schedule-pause-resume/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/pause-resume-endpoints.md, quickstart.md

**Tests**: INCLUDED. The project constitution mandates Test-First (Principle III, NON-NEGOTIABLE),
so every story's tests are written and confirmed failing before its implementation.

**Organization**: Tasks are grouped by user story. All three stories are P1 and touch the same
two source files (`src/lib/services/schedule_service.py`, `src/api/v1/endpoints/schedules.py`)
and the same two test files, so they are implemented **sequentially** (US1 → US2 → US3); see
Dependencies.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 = Pause, US2 = Resume, US3 = Owner-only/404
- Exact file paths are included in each task.

## Path Conventions

Web-service layout per plan.md: `src/api/` (FastAPI), `src/lib/` (shared), `tests/` mirrors `src/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the feature needs no new dependencies or modules.

- [X] T001 Confirm no new runtime dependencies are required and verify the `JobLookupError` import path is `from apscheduler.jobstores.base import JobLookupError`; review `pyproject.toml` (expected: no change).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Verify the reused infrastructure all stories depend on is in place. No new code.

**⚠️ CRITICAL**: Stories rely entirely on existing components — confirm them before starting.

- [X] T002 Confirm reused infrastructure exists (no code change): scheduler in `app.state` (`src/api/app.py:26`), `get_schedule_service` / `get_scheduler` (`src/api/dependencies/scheduler.py`), `get_user_id` (`src/api/dependencies/auth.py`), `ScheduleRepository.get` owner-scoping (`src/lib/repositories/schedule_repository.py:88`), `ScheduleNotFoundError` (`src/lib/exceptions/errors.py:5`), and the `asyncio.to_thread(self._scheduler.add_job, ...)` off-loop pattern (`src/lib/services/schedule_service.py:34`) to mirror.

**Checkpoint**: Foundation confirmed — user story implementation can begin.

---

## Phase 3: User Story 1 - Pause one of my schedules (Priority: P1) 🎯 MVP

**Goal**: An owner can pause an active schedule so it stops firing, without deleting it.

**Independent Test**: As the owner of an active schedule, `POST /v1/schedules/{id}/pause` returns
`200` with `next_run_time: null`, and the schedule is still retrievable via `GET`.

### Tests for User Story 1 (write first; confirm FAIL before implementing) ⚠️

- [X] T003 [P] [US1] Unit tests for `ScheduleService.pause(schedule_id, created_by)` in `tests/unit/lib/services/test_schedule_service.py`: (a) owned → calls `pause_job` off-loop and returns `ScheduleResponse` with refreshed `next_run_time`; (b) idempotent re-pause succeeds; (c) `JobLookupError` from `pause_job` → raises `ScheduleNotFoundError`. Mock `ScheduleRepository` and `AsyncIOScheduler`.
- [X] T004 [P] [US1] Integration test for `POST /v1/schedules/{schedule_id}/pause` in `tests/integration/test_schedules_api.py`: owned active schedule → `200`, response body is `ScheduleResponse` with `next_run_time` null; schedule still present on subsequent `GET`.

### Implementation for User Story 1

- [X] T005 [US1] Implement `ScheduleService.pause(schedule_id, created_by)` in `src/lib/services/schedule_service.py`: call `repo.get` (raise `ScheduleNotFoundError` if `None`); `await asyncio.to_thread(self._scheduler.pause_job, schedule_id)` catching `JobLookupError` → `ScheduleNotFoundError`; re-fetch via `repo.get` and return `_to_response`; emit structured `logger.info("schedule_paused", schedule_id=...)`. Add the `JobLookupError` import.
- [X] T006 [US1] Add `pause_schedule` handler `POST /{schedule_id}/pause` (`response_model=ScheduleResponse`) in `src/api/v1/endpoints/schedules.py` calling `service.pause(...)` with `get_user_id`, mapping `ScheduleNotFoundError` → `HTTPException(404, f"Schedule not found: {schedule_id}")` (depends on T005).

**Checkpoint**: Pause works end-to-end and is independently testable (MVP).

---

## Phase 4: User Story 2 - Resume one of my schedules (Priority: P1)

**Goal**: An owner can resume a paused schedule so it fires again on its trigger.

**Independent Test**: As the owner of a paused schedule, `POST /v1/schedules/{id}/resume` returns
`200` with `next_run_time` populated from the trigger (or `null` if the trigger is exhausted).

### Tests for User Story 2 (write first; confirm FAIL before implementing) ⚠️

- [X] T007 [P] [US2] Unit tests for `ScheduleService.resume(schedule_id, created_by)` in `tests/unit/lib/services/test_schedule_service.py`: (a) owned → calls `resume_job` off-loop, returns `ScheduleResponse` with recomputed `next_run_time`; (b) idempotent re-resume succeeds; (c) exhausted trigger → `next_run_time` null; (d) `JobLookupError` → `ScheduleNotFoundError`.
- [X] T008 [P] [US2] Integration test for `POST /v1/schedules/{schedule_id}/resume` in `tests/integration/test_schedules_api.py`: paused schedule → `200`, `next_run_time` repopulated.

### Implementation for User Story 2

- [X] T009 [US2] Implement `ScheduleService.resume(schedule_id, created_by)` in `src/lib/services/schedule_service.py`, analogous to `pause` but using `self._scheduler.resume_job`; emit `logger.info("schedule_resumed", schedule_id=...)` (same file as T005 → after T005).
- [X] T010 [US2] Add `resume_schedule` handler `POST /{schedule_id}/resume` (`response_model=ScheduleResponse`) in `src/api/v1/endpoints/schedules.py` calling `service.resume(...)`, mapping `ScheduleNotFoundError` → `404` (same file as T006 → after T006; depends on T009).

**Checkpoint**: Pause and Resume both work and are independently testable.

---

## Phase 5: User Story 3 - Pause/resume are private to the schedule owner (Priority: P1)

**Goal**: Only the creator may pause/resume; non-owners and unknown ids get `404` (no existence
disclosure); unauthenticated callers get `401`.

**Independent Test**: As user A, pausing/resuming user B's schedule returns `404`, identical to an
unknown id; calls without a credential return `401`.

### Tests for User Story 3 (write first; confirm FAIL before implementing) ⚠️

- [X] T011 [US3] Unit tests in `tests/unit/lib/services/test_schedule_service.py`: for both `pause` and `resume`, when `repo.get` returns `None` (missing or non-owned) → raises `ScheduleNotFoundError` and `pause_job`/`resume_job` is never called (same file as T003/T007 → sequential).
- [X] T012 [US3] Integration tests in `tests/integration/test_schedules_api.py` for both pause and resume: cross-owner schedule → `404` identical to unknown-id `404`; missing/invalid credential → `401`; no state change (same file as T004/T008 → sequential).

### Implementation for User Story 3

- [X] T013 [US3] Verify both handlers in `src/api/v1/endpoints/schedules.py` surface `ScheduleNotFoundError` → `404` with the same message shape as `get_schedule`/`update_schedule` (no existence disclosure). No new production code expected — ownership reuse from T005/T009 already satisfies this; adjust only if T011/T012 reveal a gap.

**Checkpoint**: All three stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Documentation and quality gates across the feature.

- [X] T014 [P] Update `documentation/api_contract.md` Pause/Resume section to reflect the implemented behaviour (200 + `ScheduleResponse`, 404 owner-scoping), referencing `specs/012-schedule-pause-resume/contracts/pause-resume-endpoints.md`.
- [X] T015 Run quality gates and fix issues: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --cov=src`.
- [ ] T016 Execute `specs/012-schedule-pause-resume/quickstart.md` manual scenarios A–E and confirm expected results. (Requires live DB + DIAL service — run manually with TEST_DB_URL set)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: After Setup. Verification only — blocks nothing in code but confirms prerequisites.
- **User Stories (Phase 3–5)**: After Foundational. **Sequential** (US1 → US2 → US3) because all three modify the same two source files and same two test files; they are not safely concurrent.
- **Polish (Phase 6)**: After all stories complete.

### User Story Dependencies

- **US1 (Pause, P1)**: Independent; the MVP.
- **US2 (Resume, P1)**: Independently testable, but its impl tasks edit the same files as US1 (`schedule_service.py`, `schedules.py`) → sequence after US1 to avoid edit conflicts.
- **US3 (Owner-only, P1)**: Adds test coverage over the ownership behaviour already wired by US1/US2; no new production code expected. Sequence after US2.

### Within Each User Story

- Tests written and FAILING before implementation (Constitution III).
- Service method before endpoint handler (endpoint calls the service).
- T006 depends on T005; T009 depends on T005 (same file); T010 depends on T009 and T006.

### Parallel Opportunities

- **Within US1**: T003 (unit test file) and T004 (integration test file) are different files → run in parallel.
- **Within US2**: T007 and T008 are different files → run in parallel.
- **Polish**: T014 is independent of T015/T016.
- Cross-story parallelism is **not** available here (shared files) — do not run US1/US2/US3 impl concurrently.

---

## Parallel Example: User Story 1

```bash
# Launch the two US1 test tasks together (different files):
Task: "Unit tests for ScheduleService.pause in tests/unit/lib/services/test_schedule_service.py"
Task: "Integration test for POST /v1/schedules/{id}/pause in tests/integration/test_schedules_api.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup → Phase 2 Foundational (verification).
2. Phase 3 US1: write T003/T004 (fail) → implement T005/T006 → tests green.
3. **STOP and VALIDATE**: pause works independently (quickstart Scenario A). Deploy/demo if ready.

### Incremental Delivery

1. US1 (Pause) → validate → demo (MVP).
2. US2 (Resume) → validate → demo.
3. US3 (Owner-only/404) → validate full ownership + auth behaviour.
4. Polish: docs + quality gates + manual quickstart.

---

## Notes

- [P] = different files, no dependency.
- All three stories are P1 and share files → sequential implementation despite independent testability.
- No new persistence, migration, schema, dependency, or module is introduced.
- Verify each story's tests fail before implementing; commit after each task or logical group.

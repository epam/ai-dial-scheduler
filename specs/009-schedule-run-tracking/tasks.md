---
description: "Task list for Schedule Run Tracking implementation"
---

# Tasks: Schedule Run Tracking

**Input**: Design documents from `specs/009-schedule-run-tracking/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: REQUIRED. The project constitution makes Test-First (TDD) NON-NEGOTIABLE â€” every
implementation task is preceded by a failing test that must be confirmed red before code is
written.

**Organization**: Tasks are grouped by the three P1 user stories (start record â†’ success record
â†’ failure record). Because this is one tightly-coupled run-lifecycle feature, the stories share
`jobs.py`, `listeners.py`, and `app.py`; story dependencies are called out explicitly.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (foundational & polish tasks carry no story label)
- Exact file paths are included in every task.

## Path Conventions

Single project: `src/` and `tests/` at repository root (per plan.md structure).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm baseline before changing shared interfaces.

- [X] T001 Confirm the existing test suite is green and record a baseline: run `uv run pytest`,
  `uv run ruff check src tests`, and `uv run mypy src` from the repo root; note any pre-existing
  failures so new red tests are distinguishable.
- [X] T002 Confirm NO new migration is required: verify `src/lib/models/schedule_run.py` and
  migration `migrations/versions/20260610_1947_092b5f51aa98_create_schedules_and_schedule_runs_.py`
  already provide `status` (CHECK includes `in_progress`/`success`/`error`), nullable `end_time`,
  nullable `result` (JSONB), `schedule_id` (FK), and NOT NULL `schedule_snapshot` (JSONB).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared schemas, the result-returning executor interface, and the persistence
repository that ALL three user stories depend on.

**âš ï¸ CRITICAL**: No user story work can begin until this phase is complete.

### Schemas

- [X] T003 [P] Add `RunOutcome(run_id: str, schedule_id: str, result: dict[str, Any])` and
  `RunStart(run_id: str, schedule_id: str, schedule_snapshot: dict[str, Any])` Pydantic models to
  `src/lib/schemas/run.py` (keep existing `RunContext` unchanged). Contract:
  `contracts/run-outcome.md`, `contracts/schedule-run-repository.md`.

### Executor interface change (result-returning) â€” TDD

- [X] T004 [P] Update executor unit tests to assert returned result payloads (red first):
  in `tests/unit/worker/executors/test_rest.py` assert `execute(...)` returns
  `{"status_code": int, "body": ...}`; in `tests/unit/worker/executors/test_sse.py` assert it
  returns `{"events": [...]}`; in `tests/unit/worker/executors/test_chat_completion.py` assert it
  returns `{"result": ...}` (non-stream) / `{"chunks": [...]}` (stream). Shapes per
  `contracts/run-outcome.md`.
- [X] T005 Change the executor contract in `src/worker/executors/base.py`:
  `Executor.execute(...) -> dict[str, Any]`.
- [X] T006 [P] Make `src/worker/executors/rest.py` return `{"status_code", "body"}` (parsed JSON
  when JSON, else text) while keeping the existing `run_result` log and `ScheduleRunError` on
  failure.
- [X] T007 [P] Make `src/worker/executors/sse.py` accumulate events into `{"events": [...]}` and
  return it, keeping per-event `sse_event` logs and failure handling.
- [X] T008 [P] Make `src/worker/executors/chat_completion.py` accumulate and return
  `{"result": ...}` / `{"chunks": [...]}`, keeping per-chunk/result logs and failure handling.

### Persistence repository â€” TDD

- [X] T009 [P] Write failing unit tests in
  `tests/unit/lib/repositories/test_schedule_run_repository.py` for `create_in_progress`
  (inserts `id=UUID(run_id)`, `status="in_progress"`, `end_time=None`, `result=None`,
  `schedule_id`, `schedule_snapshot`), `mark_success` (sets `success`, `end_time`, `result`;
  returns `None` when no row), and `mark_error` (sets `error`, `end_time`,
  `result={"error_text": ...}`; returns `None` when no row).
- [X] T010 Implement `ScheduleRunRepository` in
  `src/lib/repositories/schedule_run_repository.py` with `create_in_progress(run: RunStart)`,
  `mark_success(run_id, result)`, `mark_error(run_id, error_text)` per
  `contracts/schedule-run-repository.md` (SQLAlchemy/SQLModel only, `datetime.now(UTC)`,
  `str`â†’`uuid.UUID` at the boundary, commits its own transaction, returns `None` on missing row).

**Checkpoint**: Schemas, result-returning executors, and the repository exist and their unit
tests pass â€” user stories can now build on them.

---

## Phase 3: User Story 1 - Record that a run started (Priority: P1) ðŸŽ¯ MVP

**Goal**: When a non-heartbeat job begins, an `in_progress` row is persisted (no end time, no
result, correct `schedule_id`, parameter snapshot) before any work â€” so even a crashed run
leaves evidence. The heartbeat job creates no row.

**Independent Test**: Fire a job and, before completion, observe one `schedule_runs` row with
`status="in_progress"`, `end_time IS NULL`, `result IS NULL`, the correct `schedule_id`, and
`schedule_snapshot` equal to the job kwargs; fire the heartbeat and observe no row.

### Tests for User Story 1 (write first, confirm RED) âš ï¸

- [X] T011 [P] [US1] In `tests/unit/worker/test_jobs.py` add tests that `execute_scheduled_job`
  creates an `in_progress` run at start via `ScheduleRunRepository.create_in_progress` (mock the
  repo/session): assert it is called with `run_id`, `schedule_id`, and a JSON-safe
  `schedule_snapshot` built from kwargs; assert creation happens BEFORE validation (so invalid
  kwargs still produce an `in_progress` row); assert a `run_created` log is emitted; assert that a
  `create_in_progress` failure is caught, logged, and does not abort the run.

### Implementation for User Story 1

- [X] T012 [US1] In `src/worker/jobs.py` `execute_scheduled_job`, after generating `run_id` and
  binding context, build a JSON-safe `schedule_snapshot` from `kwargs` (coerce enum values to
  strings) and call `ScheduleRunRepository.create_in_progress(RunStart(...))` inside
  `async with Database().sessionmaker() as session`, wrapped in `try/except` (log failure, do not
  abort). This MUST run before `ScheduleProperties(**kwargs)` validation. Log `run_created`.

**Checkpoint**: In-progress rows are persisted for fired jobs (and not for the heartbeat); US1 is
independently testable.

---

## Phase 4: User Story 2 - Record a successful run with its results (Priority: P1)

**Goal**: A successful job returns a standardized `RunOutcome`; an `EVENT_JOB_EXECUTED` listener
updates the matching row (by `run_id`) to `success` with `end_time` and the produced `result`.
The heartbeat (returns `None`) is skipped.

**Independent Test**: Fire a job that succeeds and confirm its row transitions to
`status="success"` with `end_time` set and `result` holding the executor's output; fire the
heartbeat and confirm the listener logs `run_persist_skip` and writes no row.

**Depends on**: US1 (the `in_progress` row + `jobs.py` changes) and Foundational (repository,
`RunOutcome`, executors).

### Tests for User Story 2 (write first, confirm RED) âš ï¸

- [X] T013 [P] [US2] In `tests/unit/worker/test_jobs.py` add a test that `execute_scheduled_job`
  RETURNS `RunOutcome(run_id, schedule_id, result=<executor return>)` on success (mock
  `resolve_executor` to return a result dict).
- [X] T014 [P] [US2] Create `tests/unit/worker/test_listeners.py` with tests for the
  `on_job_executed` listener built by `build_run_listeners(tracker)`: on `event.retval` being a
  `RunOutcome` it schedules a task that calls `mark_success(run_id, result)` and logs
  `run_recorded_success`; on `event.retval` being `None` (heartbeat) it logs `run_persist_skip`
  and schedules nothing; when `mark_success` returns `None` it logs `run_record_missing`. Run the
  scheduled coroutine on a real loop and assert via `structlog.testing.capture_logs`.
- [X] T015 [P] [US2] In `tests/unit/worker/test_worker_app.py` add tests that the
  `EVENT_JOB_EXECUTED` listener is registered and that pending tracker tasks are drained on
  shutdown.
- [X] T016 [P] [US2] Create `tests/integration/test_schedule_run_lifecycle.py` success scenario:
  fire a REST job through a real `AsyncIOScheduler` (mock transport) and assert the
  `schedule_runs` row goes `in_progress` â†’ `success` with `result` populated (per
  `quickstart.md` Scenario 1).

### Implementation for User Story 2

- [X] T017 [US2] In `src/worker/jobs.py` `execute_scheduled_job`, capture the executor's returned
  result and `return RunOutcome(run_id=run_id, schedule_id=schedule_id, result=result)` on
  success (depends on T012).
- [X] T018 [US2] Create `src/worker/listeners.py` with
  `build_run_listeners(tracker: set[asyncio.Task]) -> (on_job_executed, on_job_error)`; implement
  `on_job_executed` + `_record_success(outcome)` per `contracts/run-listeners.md` (skip non-
  `RunOutcome` retval with `run_persist_skip`; schedule a tracked loop task via
  `asyncio.get_running_loop().create_task`; done-callback discards from `tracker` and logs
  failures; open session â†’ `mark_success`; log `run_recorded_success` or `run_record_missing`).
  Leave `on_job_error` as a minimal stub to be completed in US3.
- [X] T019 [US2] In `src/worker/app.py` `_run`/`run_worker`, create `tracker: set[asyncio.Task]`,
  build listeners via `build_run_listeners(tracker)`, register `on_job_executed` on
  `EVENT_JOB_EXECUTED`, and drain the tracker on shutdown with
  `await asyncio.gather(*tracker, return_exceptions=True)` before/around `scheduler.shutdown`.

**Checkpoint**: Successful runs are persisted with their results; heartbeat is skipped; US1+US2
work independently.

---

## Phase 5: User Story 3 - Record a failed run with its reason (Priority: P1)

**Goal**: A failed job raises `ScheduleRunError(run_id, schedule_id)`; an `EVENT_JOB_ERROR`
listener updates the matching row to `status="error"` with `end_time` and
`result={"error_text": str(exc)}`. Validation failures are recorded too. Non-`ScheduleRunError`
failures are logged but not persisted.

**Independent Test**: Fire a job that fails (transport error or invalid kwargs) and confirm its
row transitions to `status="error"` with `end_time` set and `result.error_text` populated.

**Depends on**: US2 (the `listeners.py` factory + `app.py` tracker/drain wiring) and Foundational
(repository `mark_error`).

### Tests for User Story 3 (write first, confirm RED) âš ï¸

- [X] T020 [P] [US3] In `tests/unit/worker/test_listeners.py` add tests for `on_job_error`: when
  `event.exception` is a `ScheduleRunError` it logs `run_failed` and schedules a tracked task that
  calls `mark_error(run_id, str(exc))` and logs `run_recorded_error`; when the exception is NOT a
  `ScheduleRunError` it logs `job_error` with `event.job_id` and schedules nothing; when
  `mark_error` returns `None` it logs `run_record_missing`.
- [X] T021 [P] [US3] In `tests/unit/worker/test_worker_app.py` update/assert that the
  `EVENT_JOB_ERROR` listener registered is the one from `build_run_listeners` (superseding the old
  standalone `on_job_error`).
- [X] T022 [P] [US3] In `tests/integration/test_schedule_run_lifecycle.py` add the failure
  scenario: fire a job whose target errors (and/or a job with invalid kwargs) and assert the row
  goes `in_progress` â†’ `error` with `result.error_text` set (per `quickstart.md` Scenario 2).

### Implementation for User Story 3

- [X] T023 [US3] Complete `on_job_error` + `_record_error(exc)` in `src/worker/listeners.py` per
  `contracts/run-listeners.md` (fold in the existing generic `job_error` logging for non-
  `ScheduleRunError`; for `ScheduleRunError` log `run_failed`, schedule a tracked task â†’
  `mark_error`; log `run_recorded_error` or `run_record_missing`).
- [X] T024 [US3] In `src/worker/app.py`, register the `on_job_error` listener from
  `build_run_listeners` on `EVENT_JOB_ERROR` and remove the now-superseded standalone
  `on_job_error` function (update its imports/usages).

**Checkpoint**: Full lifecycle persisted â€” start, success, and failure. All three stories work.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates, cleanup, and end-to-end validation across all stories.

- [X] T025 [P] Update/clean `tests/unit/worker/test_worker_app.py` to remove assertions tied to
  the deleted standalone `on_job_error` and ensure both listeners' registration is covered.
- [X] T026 Run and fix lint/format: `uv run ruff check src tests` and
  `uv run ruff format src tests`.
- [X] T027 Run and fix types: `uv run mypy src` (resolve any new annotations, esp. the
  `set[asyncio.Task]` tracker and `dict[str, Any]` executor returns).
- [X] T028 Run the full suite with coverage:
  `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml` and ensure all new
  code is covered.
- [X] T029 Execute `quickstart.md` Scenarios 1â€“4 (success, failure, heartbeat-skip, missing-row
  discrepancy) and confirm logged events (`run_created`, `run_recorded_success`, `run_failed`,
  `run_recorded_error`, `run_persist_skip`, `run_record_missing`) appear as specified.
- [X] T030 Verify FR-012: confirm no credentials/`Api-Key` values appear in any persisted
  `result`/`error_text` or in logs across the executors and listeners.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies â€” start immediately.
- **Foundational (Phase 2)**: Depends on Setup â€” BLOCKS all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational.
- **User Story 2 (Phase 4)**: Depends on Foundational + US1 (shares `jobs.py`; updates rows US1
  creates).
- **User Story 3 (Phase 5)**: Depends on Foundational + US2 (shares `listeners.py` factory and
  `app.py` tracker/drain wiring).
- **Polish (Phase 6)**: Depends on all user stories.

### Within Each User Story

- Tests are written and confirmed FAILING before implementation (constitution III).
- Schemas/repository (Foundational) before job/listener logic.
- `jobs.py` start-record (US1) before `jobs.py` success-return (US2).
- `listeners.py` factory + executed listener (US2) before error listener (US3).

### Parallel Opportunities

- Foundational: T003, T004, T009 are [P] (different files); executor impls T006/T007/T008 are [P]
  after T005.
- US2 tests T013â€“T016 are [P] (different test files/areas) and can be written together.
- US3 tests T020â€“T022 are [P].
- Polish T025 is [P]; T026â€“T030 are mostly sequential gate runs.

---

## Parallel Example: Foundational Phase

```bash
# Author these independent pieces in parallel:
Task: "Add RunOutcome/RunStart schemas in src/lib/schemas/run.py"          # T003
Task: "Red executor result-shape tests in tests/unit/worker/executors/*"   # T004
Task: "Red repository tests in tests/unit/lib/repositories/test_schedule_run_repository.py"  # T009

# After base.py signature change (T005), implement the three executors in parallel:
Task: "rest.py returns {status_code, body}"           # T006
Task: "sse.py returns {events: [...]}"                # T007
Task: "chat_completion.py returns {result}/{chunks}"  # T008
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 Setup â†’ Phase 2 Foundational (CRITICAL â€” blocks everything).
2. Phase 3 US1 â†’ STOP and VALIDATE: fired jobs leave an `in_progress` row; heartbeat leaves none.
3. This is the minimal durable-evidence increment.

### Incremental Delivery

1. Foundational ready â†’ US1 (start records) â†’ US2 (success + results) â†’ US3 (failure + reasons).
2. Each story is independently testable; US2/US3 extend shared files US1/US2 introduced.
3. Finish with Phase 6 quality gates and quickstart validation.

---

## Notes

- [P] = different files, no incomplete dependencies.
- Per constitution SDLC, run `/speckit-implement` in a separate terminal with a mid-capability
  model (Sonnet); it enforces TDD, runs ruff/mypy/pytest, and loops the code-review-agent.
- No new Alembic migration is created (the `schedule_runs` table already exists).
- Commit after each task or logical group; verify red tests before implementing.

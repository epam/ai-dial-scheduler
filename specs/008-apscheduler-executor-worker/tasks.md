# Tasks: APScheduler Executor Worker

**Input**: Design documents from `specs/008-apscheduler-executor-worker/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: INCLUDED — the constitution mandates TDD (Principle III). Every implementation task is
preceded by a failing test task.

**Organization**: Tasks are grouped by user story (US1 P1, US2 P1, US3 P2) for independent
implementation and testing. Config is taken from the fired job's kwargs — the dispatch path does
**no** DB read and has **no** missing-row path (locked decision D1/D2).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 (omitted for Setup, Foundational, Polish)

## Path Conventions

Single-repo worker service: `src/worker/` (entry point) depends on `src/lib/` (shared). Tests
mirror `src/` under `tests/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies and configuration shared by all stories.

- [X] T001 Add `openai` and `httpx-sse` to `[project].dependencies` in `pyproject.toml` (pin current releases compatible with `httpx==0.28.1`) and run `uv sync` to update `uv.lock`
- [X] T002 [P] Add `noop_interval_seconds: int` and `http_timeout_seconds: float` to `Settings` in `src/lib/settings.py`
- [X] T003 [P] Add constants (heartbeat job id + name, `Api-Key` header name, SSE `Accept: text/event-stream` value) to `src/lib/constants.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared types and the worker resource context that every story builds on.

**⚠️ CRITICAL**: No user-story work can begin until this phase is complete.

- [X] T004 [P] Add `ScheduleRunError(SchedulerError)` carrying `schedule_id` and `run_id` in `src/lib/exceptions/__init__.py`
- [X] T005 [P] Add `RunContext` and `RunResult` Pydantic v2 models (logging shape, no persistence) in `src/lib/schemas/run.py`
- [X] T006 [P] Define the `Executor` interface (`async def execute(self, run_id: str, properties: ScheduleProperties) -> None`) in `src/worker/executors/base.py` and create `src/worker/executors/__init__.py`
- [X] T007 [P] Write failing tests for `WorkerContext` set/get/clear lifecycle in `tests/unit/worker/test_context.py`
- [X] T008 Implement `WorkerContext` (holds shared `httpx.AsyncClient`) and `set_worker_context`/`get_worker_context`/`clear_worker_context` in `src/worker/context.py` (depends on T002, T007)

**Checkpoint**: Shared types, executor interface, and resource accessor ready.

---

## Phase 3: User Story 1 - Execute a fired scheduled task (Priority: P1) 🎯 MVP

**Goal**: When a job fires, validate its kwargs as `ScheduleProperties`, branch by `target_type`,
and log results as they arrive for all three executors.

**Independent Test**: Add a job to the shared store referencing `EXECUTE_SCHEDULED_JOB_REF` with
valid kwargs for each `target_type`; on fire, confirm the matching call is made and its result(s)
logged with `run_id`/`schedule_id`.

### Tests for User Story 1 (write first, must FAIL)

- [X] T009 [P] [US1] Happy-path tests for `RestExecutor` (method/url/payload, `Api-Key`+`extra_headers` merge with extra winning, timeout applied, 2xx → one log record) in `tests/unit/worker/executors/test_rest.py`
- [X] T010 [P] [US1] Happy-path tests for `SseExecutor` (`Accept: text/event-stream` set, per-event `{event_name: raw_string}` logging, zero-event stream succeeds) in `tests/unit/worker/executors/test_sse.py`
- [X] T011 [P] [US1] Happy-path tests for `ChatCompletionExecutor` (azure_endpoint = scheme+netloc, deployment from URL path, `method` ignored, single-response → one record, stream → one record per chunk) in `tests/unit/worker/executors/test_chat_completion.py`
- [X] T012 [P] [US1] Tests for dispatcher `TargetType → Executor` resolution and kwargs→`ScheduleProperties` validation success in `tests/unit/worker/test_dispatcher.py`
- [X] T013 [P] [US1] Tests for dispatch handler happy path (generates `run_id`, binds `run_id`/`schedule_id` log context, validates kwargs, calls resolved executor) in `tests/unit/worker/test_jobs.py`
- [X] T014 [P] [US1] Integration test firing a `rest` job end-to-end through a real `AsyncIOScheduler` in `tests/integration/test_worker_dispatch.py`

### Implementation for User Story 1

- [X] T015 [P] [US1] Implement `RestExecutor` (shared client from `WorkerContext`, header merge, `http_timeout_seconds`, log one record, raise `ScheduleRunError` on non-2xx/transport) in `src/worker/executors/rest.py`
- [X] T016 [P] [US1] Implement `SseExecutor` (same request shape + `Accept` header, `httpx_sse.aconnect_sse`, per-event logging, raise on non-2xx) in `src/worker/executors/sse.py`
- [X] T017 [P] [US1] Implement `ChatCompletionExecutor` (parse endpoint+deployment from `url`, per-call `AsyncAzureOpenAI` with `dial_api_key`/`api_version`, `create(**payload, extra_headers=...)`, single vs. streamed logging) in `src/worker/executors/chat_completion.py`
- [X] T018 [US1] Implement dispatcher `TargetType → Executor` map and `resolve()` in `src/worker/dispatcher.py` (depends on T015–T017, T006)
- [X] T019 [US1] Implement dispatch handler `execute_scheduled_job(schedule_id, **kwargs)` in `src/worker/jobs.py`: generate `run_id`, bind log context, validate kwargs as `ScheduleProperties`, branch via dispatcher (depends on T018, T004, T005)
- [X] T020 [US1] Wire `WorkerContext` lifecycle in `run_worker` (create `httpx.AsyncClient` with `http_timeout_seconds`, `set_worker_context`, close/clear on shutdown) and call `init_telemetry` in `src/worker/app.py` (depends on T008)

**Checkpoint**: All three target types execute and log on fire — MVP is demonstrable.

---

## Phase 4: User Story 2 - Fail a run safely with full diagnostic context (Priority: P1)

**Goal**: Any failure raises a single `ScheduleRunError` carrying `schedule_id`+`run_id`, surfaced
by an `EVENT_JOB_ERROR` listener that logs it, without interrupting other runs.

**Independent Test**: Fire jobs with invalid kwargs, a non-2xx target, and a transport error;
confirm each yields exactly one logged failure with both ids, and a failure in one run does not
stop another.

**Depends on**: US1 (executors, dispatcher, handler, and app wiring must exist).

### Tests for User Story 2 (write first, must FAIL)

- [X] T021 [P] [US2] Tests for validation-failure path → `ScheduleRunError(schedule_id, run_id)` in `tests/unit/worker/test_jobs.py`
- [X] T022 [P] [US2] Tests that `RestExecutor` non-2xx/transport errors surface as `ScheduleRunError` in `tests/unit/worker/executors/test_rest.py`
- [X] T023 [P] [US2] Tests that `SseExecutor` non-2xx surfaces as `ScheduleRunError` in `tests/unit/worker/executors/test_sse.py`
- [X] T024 [P] [US2] Tests for the `EVENT_JOB_ERROR` listener (logs `schedule_id`+`run_id` from `event.exception`) and run isolation in `tests/unit/worker/test_worker_app.py`

### Implementation for User Story 2

- [X] T025 [US2] Wrap the dispatch body in `try/except` and re-raise all failures as `ScheduleRunError(schedule_id, run_id)` (validation + executor errors) in `src/worker/jobs.py` (depends on T019)
- [X] T026 [US2] Add `on_job_error` listener (extracts ids from `ScheduleRunError`, logs them) and register it with `EVENT_JOB_ERROR` in `src/worker/app.py` (depends on T020)

**Checkpoint**: Every failure mode produces one identified, logged failure; runs are isolated.

---

## Phase 5: User Story 3 - Confirm the worker is alive (Priority: P2)

**Goal**: A periodic no-op heartbeat in a non-persistent in-memory jobstore logs timestamp + job
name and nothing else.

**Independent Test**: Start the worker with no task jobs; confirm a heartbeat logs on each
`noop_interval_seconds` and never appears in the shared `apscheduler_jobs` store.

**Depends on**: T020 (app wiring).

### Tests for User Story 3 (write first, must FAIL)

- [X] T027 [P] [US3] Test the `heartbeat` handler logs job name + ISO-8601 UTC timestamp only in `tests/unit/worker/test_jobs.py`
- [X] T028 [P] [US3] Test heartbeat is registered on an interval in a `MemoryJobStore` (not the shared `default` store) in `tests/unit/worker/test_worker_app.py`

### Implementation for User Story 3

- [X] T029 [P] [US3] Implement the `heartbeat` handler (log job name + `datetime.now(UTC)`, no other work) in `src/worker/jobs.py`
- [X] T030 [US3] Add a `MemoryJobStore` (alias `local`) in `create_worker` and register the heartbeat `IntervalTrigger` job (id/name from constants, `replace_existing=True`) in `run_worker` in `src/worker/app.py` (depends on T020)

**Checkpoint**: Worker liveness is observable independent of task jobs.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T031 [P] Extend `tests/unit/lib/test_settings.py` for the new `noop_interval_seconds`/`http_timeout_seconds` defaults and overrides
- [X] T032 [P] Verify `dial_api_key` never appears in any log record (assertion in executor tests) — Security/Constitution check
- [X] T033 Run quality gates and fix issues: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --cov=src`
- [ ] T034 Execute `quickstart.md` validation scenarios (heartbeat, three target types, failure paths, no-persistence)

---

## Dependencies & Execution Order

### Phase order
- **Setup (P1)** → **Foundational (P2)** → **US1 (P3)** → **US2 (P4)** → **US3 (P5)** → **Polish (P6)**.
- Foundational blocks all stories. US2 and US3 depend on US1's app wiring (T020) and US1 files.

### Cross-phase file ownership (forces sequencing)
- `src/worker/app.py`: T020 (US1) → T026 (US2) → T030 (US3) — same file, sequential.
- `src/worker/jobs.py`: T019 (US1) → T025 (US2) → T029 (US3) — same file, sequential.
- `tests/unit/worker/test_jobs.py`: T013 → T021 → T027 (sequential).
- `tests/unit/worker/test_worker_app.py`: T024 (US2) → T028 (US3) (sequential).
- `tests/unit/worker/executors/test_rest.py`: T009 → T022; `test_sse.py`: T010 → T023.

### Within each story
- Tests (T0xx) MUST be written and FAIL before their implementation tasks.
- Executors (T015–T017) before dispatcher (T018) before handler (T019).

## Parallel Opportunities

- **Setup**: T002, T003 in parallel (after/with T001).
- **Foundational**: T004, T005, T006, T007 in parallel; T008 after T007.
- **US1 tests**: T009–T014 all in parallel (distinct files).
- **US1 impl**: T015, T016, T017 in parallel (distinct executor files); then T018 → T019; T020 in parallel with T018/T019 (distinct file, depends only on T008).
- **US2 tests**: T021–T024 in parallel (distinct files).
- **US3 tests**: T027, T028 in parallel; **US3 impl**: T029, T030 in parallel (distinct files).
- **Polish**: T031, T032 in parallel.

### Parallel example — US1 implementation

```bash
Task: "Implement RestExecutor in src/worker/executors/rest.py"
Task: "Implement SseExecutor in src/worker/executors/sse.py"
Task: "Implement ChatCompletionExecutor in src/worker/executors/chat_completion.py"
```

## Implementation Strategy

### MVP first (US1 only)
1. Setup (Phase 1) → Foundational (Phase 2).
2. US1 (Phase 3): tests → executors → dispatcher → handler → app wiring.
3. **STOP and VALIDATE**: fire one job per target type; confirm logged results. Demoable MVP.

### Incremental delivery
1. + US2 → all failures identified, logged, isolated.
2. + US3 → heartbeat liveness.
3. Polish → settings tests, secret-safety, quality gates, quickstart.

---

## Notes

- [P] = different files, no dependency on an incomplete task.
- Verify each test FAILS before implementing (TDD, Constitution III).
- No DB read in the dispatch path; no `schedule_runs` persistence (FR-016 / SC-007).
- `EXECUTE_SCHEDULED_JOB_REF` path (`src.worker.jobs:execute_scheduled_job`) is unchanged — no creator migration needed.

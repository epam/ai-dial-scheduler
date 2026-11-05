---
description: "Task list for Schedule Timeout & Retry"
---

# Tasks: Schedule Timeout & Retry

**Input**: Design documents from `/specs/020-schedule-timeout-retry/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/schedule-properties.md, quickstart.md

**Tests**: INCLUDED — the project constitution makes Test-First (TDD) NON-NEGOTIABLE. Every implementation task is preceded by a test task that MUST be written and confirmed failing first.

**Organization**: Tasks are grouped by user story. US1 (timeout) is the MVP and introduces the shared worker orchestrator; US2 (retry) and US3 (observability) extend it.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (setup, foundational, polish carry no story label)

## Path Conventions

Web service with shared library: `src/lib/` (shared), `src/api/` (FastAPI), `src/worker/` (APScheduler). Tests mirror `src/` under `tests/`. No database migration is required for this feature (new data rides existing JSONB columns).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Minimal groundwork shared across all stories.

- [X] T001 [P] Add structured log event-name constants for this feature (`attempt_start`, `retry_scheduled`, `attempt_failed_non_retryable`, `last_retry_failed`) to `src/lib/constants.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared data contract (schema + exception) that all three stories read. Fields are defined here; per-story behaviour is added later.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 [P] Write unit tests for schema validation in `tests/lib/schemas/test_schedule.py`: accept valid `timeout` and `retry`; reject `timeout <= 0`, empty `backoff_interval`, `backoff_interval` with non-positive elements, empty `retryable_errors`, and `retryable_errors` outside `[100, 599]`. Confirm tests FAIL.
- [X] T003 Implement `RetryPolicy` model and add nullable `timeout: int | None` and `retry: RetryPolicy | None` fields with their validators to `ScheduleProperties` in `src/lib/schemas/schedule.py` (makes T002 pass; existing target-rule/url validators untouched)
- [X] T004 [P] Write unit test asserting `ScheduleRunError` accepts an optional `status_code` in `tests/lib/exceptions/test_errors.py`. Confirm it FAILS.
- [X] T005 Add optional `status_code: int | None = None` attribute to `ScheduleRunError` in `src/lib/exceptions/errors.py` (makes T004 pass; keep `schedule_id`/`run_id` behaviour)

**Checkpoint**: Shared schema and exception contract ready — `timeout`/`retry` now flow through `kwargs` → `schedule_snapshot` → `ScheduleProperties(**kwargs)` and appear in `ScheduleResponse.properties`.

---

## Phase 3: User Story 1 - Configure a per-schedule timeout (Priority: P1) 🎯 MVP

**Goal**: Each scheduled execution is abandoned at the schedule's `timeout` (integer seconds); when `timeout` is absent, the default application timeout (`worker_http.timeout_seconds`) applies.

**Independent Test**: Create a schedule with a small `timeout` pointing at a slow target → run is abandoned at the limit and recorded as `error`. Create one without `timeout` → it uses the default.

### Tests for User Story 1 (write first, must FAIL)

- [X] T006 [P] [US1] Unit tests for the timeout orchestrator in `tests/worker/test_retry.py`: effective-timeout resolution (`properties.timeout` else default); a slow attempt is abandoned at the limit via `asyncio.wait_for`; `asyncio.TimeoutError` becomes a terminal failure (no retry). Confirm FAIL.
- [X] T007 [P] [US1] Tests that each executor applies the per-attempt timeout to its underlying call in `tests/worker/executors/test_rest.py`, `test_sse.py`, `test_chat_completion.py`. Confirm FAIL.
- [X] T008 [P] [US1] Integration test in `tests/worker/test_jobs.py`: a scheduled job with a small `timeout` against a slow target is abandoned at the limit and persisted as `error`; a job without `timeout` uses the default. Confirm FAIL.

### Implementation for User Story 1

- [X] T009 [US1] Create `src/worker/retry.py` with an async orchestrator that builds the effective timeout (`properties.timeout or get_settings().worker_http.timeout_seconds`), runs a single executor attempt wrapped in `asyncio.wait_for(...)`, returns the executor result, and raises on timeout (makes T006 pass)
- [X] T010 [US1] Apply per-attempt timeout passthrough (httpx per-request `timeout=`) in `src/worker/executors/rest.py`
- [X] T011 [P] [US1] Apply per-attempt timeout passthrough (stream `timeout=`) in `src/worker/executors/sse.py`
- [X] T012 [P] [US1] Apply per-attempt timeout passthrough (OpenAI `.create(timeout=...)`) in `src/worker/executors/chat_completion.py`
- [X] T013 [US1] Wire `src/worker/jobs.py` `_execute_scheduled_job_inner` to call the orchestrator (build `ScheduleProperties`, build `RunContext`, invoke orchestrator) instead of calling the executor directly, preserving existing success/error persistence (depends on T009)

**Checkpoint**: Per-schedule timeout fully functional and independently testable. MVP complete.

---

## Phase 4: User Story 2 - Configure automatic retry with backoff (Priority: P1)

**Goal**: Failed attempts whose HTTP status code is in `retryable_errors` are retried, waiting `backoff_interval[i]` before retry *i*; total retries = `len(backoff_interval)`. Retrying stops on success. Without `retry`, behaviour is a single attempt. Every attempt is recorded in logs (not the DB).

**Independent Test**: Target returns `500` then `200` with `retry={backoff_interval:[2,5], retryable_errors:[500]}` → waits 2s, retries, succeeds. Always-`500` target with three intervals → 4 attempts then final `error`. `403` with `retryable_errors:[500]` → one attempt, immediate `error`.

### Tests for User Story 2 (write first, must FAIL)

- [X] T014 [P] [US2] Unit tests for the retry loop in `tests/worker/test_retry.py`: retries exactly `len(backoff_interval)` times on a retryable status; stops on success; non-retryable status fails immediately; timeout is not retried; backoff intervals are slept in order (patch `asyncio.sleep`); builds the `attempts` list with correct `is_retry`/`is_last_attempt`. Confirm FAIL.
- [X] T015 [P] [US2] Tests that executors populate `ScheduleRunError.status_code` from HTTP/SDK status errors in `tests/worker/executors/test_rest.py`, `test_sse.py`, `test_chat_completion.py`. Confirm FAIL.
- [X] T016 [P] [US2] (Revised per product decision — attempts are logs-only, not persisted.) Repository tests in `tests/unit/lib/repositories/test_schedule_run_repository.py` confirm `mark_success`/`mark_error` persist only the final outcome (no `attempts` in `result`).
- [X] T017 [P] [US2] Integration tests in `tests/integration/test_schedule_timeout_retry.py`: retry-then-success persists final `success` payload only; exhausted retries persist `error_text` only; per-attempt detail is asserted via captured logs.

### Implementation for User Story 2

- [X] T018 [US2] Extend the orchestrator in `src/worker/retry.py` with the retry loop: on `ScheduleRunError` whose `status_code` ∈ `retry.retryable_errors` and retries remaining, `await asyncio.sleep(backoff_interval[i])` then retry; log each attempt; return `RunOutcome(succeeded, result, error, attempt_count)` (no attempt records persisted) (depends on T009, T005)
- [X] T019 [US2] Populate `ScheduleRunError.status_code` from `httpx.HTTPStatusError.response.status_code` in `src/worker/executors/rest.py` (depends on T005)
- [X] T020 [P] [US2] Populate `status_code` from `httpx.HTTPStatusError` in `src/worker/executors/sse.py` (depends on T005)
- [X] T021 [P] [US2] Populate `status_code` from `openai.APIStatusError.status_code` in `src/worker/executors/chat_completion.py` (depends on T005)
- [X] T022 [US2] (Revised — no DB change.) `mark_success`/`mark_error` in `src/lib/repositories/schedule_run_repository.py` remain final-outcome only; no `attempts` persisted.
- [X] T023 [US2] Update `src/worker/jobs.py` to consume `RunOutcome` from the orchestrator, persist the final outcome, and log the `attempt_count` summary (depends on T018)

**Checkpoint**: Retry with backoff fully functional; every attempt logged (final outcome only persisted). US1 and US2 both work.

---

## Phase 5: User Story 3 - Observe timeout and retry behavior in logs (Priority: P2)

**Goal**: Structured logs let an operator determine, from logs alone, the applied timeout, each attempt number, which attempts were retries, the backoff waited, the triggering status code, and that the final attempt was the last retry and failed.

**Independent Test**: Run an exhausted-retry scenario and inspect logs — each attempt labeled, retries labeled with backoff and status code, and a final `last_retry_failed` entry; every entry carries `schedule_id`/`run_id`.

### Tests for User Story 3 (write first, must FAIL)

- [X] T024 [P] [US3] Tests asserting emitted structured log events in `tests/worker/test_retry.py`: `attempt_start` (with `attempt`, `is_retry`, applied `timeout`); `retry_scheduled` (with `next_attempt`, `backoff_seconds`, `status_code`); `attempt_failed_non_retryable` (with `status_code`); `last_retry_failed` on exhaustion. Confirm FAIL.

### Implementation for User Story 3

- [X] T025 [US3] Add structured `logger` calls in `src/worker/retry.py` at each attempt start, before each retry wait, on non-retryable failure, and on last-retry failure — including applied timeout and retry-policy context, using the constants from T001 (`schedule_id`/`run_id` already bound to contextvars by `jobs.py`)
- [X] T026 [US3] Ensure final success/failure logs in `src/worker/jobs.py` include an attempt-count / retry summary so a completed run is summarizable from logs

**Checkpoint**: Full observability of timeout and retry behaviour.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verification, backward compatibility, and quality gates across all stories.

- [X] T027 [P] Add a backward-compatibility test in `tests/worker/test_jobs.py`: a schedule whose stored `kwargs` lack `timeout`/`retry` executes with the default timeout and a single attempt (no `attempts` assumptions broken)
- [X] T028 [P] Verify `ScheduleResponse.properties` round-trips `timeout`/`retry` (or their absence) via an API-level test under `tests/api/` (or `tests/lib/services/test_schedule_service.py`), per contract `contracts/schedule-properties.md`
- [X] T029 Run quality gates and fix issues: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --cov=src`
- [X] T030 Execute `specs/020-schedule-timeout-retry/quickstart.md` Scenarios A–H and confirm expected behaviour

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories (shared schema + exception).
- **US1 (Phase 3)**: Depends on Foundational. Creates the shared `src/worker/retry.py` orchestrator. MVP.
- **US2 (Phase 4)**: Depends on Foundational + US1 (extends the orchestrator created in T009; uses `status_code` from T005).
- **US3 (Phase 5)**: Depends on US1 (and is most meaningful after US2, since it logs retry behaviour).
- **Polish (Phase 6)**: Depends on all desired stories.

### Within Each Story

- Test tasks precede implementation tasks and MUST fail first (Constitution III).
- Orchestrator (`retry.py`) before `jobs.py` wiring; `ScheduleRunError.status_code` (foundational) before executor status capture.

### Parallel Opportunities

- T001 runs alone in Setup.
- Foundational: T002 ∥ T004 (different test files); T003 and T005 touch different files and can proceed once their tests exist.
- US1: T006 ∥ T007 ∥ T008 (test files); T011 ∥ T012 (different executor files) in parallel with each other; T010 and T013 serialize around `retry.py`/`jobs.py`.
- US2: T014 ∥ T015 ∥ T016 ∥ T017 (test files); T020 ∥ T021 (different executor files).
- US3: T024 alone; then T025, T026.
- Polish: T027 ∥ T028.

---

## Parallel Example: User Story 1

```bash
# Write the failing tests together first:
Task: "Unit tests for timeout orchestrator in tests/worker/test_retry.py"
Task: "Executor timeout-passthrough tests in tests/worker/executors/test_rest.py|test_sse.py|test_chat_completion.py"
Task: "Integration timeout test in tests/worker/test_jobs.py"

# Then parallelize executor passthrough edits (different files):
Task: "Timeout passthrough in src/worker/executors/sse.py"
Task: "Timeout passthrough in src/worker/executors/chat_completion.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → Phase 2 Foundational → Phase 3 US1.
2. **STOP and VALIDATE**: per-schedule timeout works; defaults preserved; existing tests green.
3. Deploy/demo (timeout-only is a complete, valuable increment).

### Incremental Delivery

1. Foundation + US1 → timeout (MVP).
2. US2 → automatic retry with backoff + per-attempt logging (final outcome persisted only).
3. US3 → observability for retry/timeout.
4. Polish → backward-compat, contract round-trip, quality gates, quickstart.

---

## Notes

- No Alembic migration and no run-schema change: `timeout`/`retry` persist in `schedules.kwargs` JSONB; the run `result` is unchanged (final outcome only). Per-attempt detail is logs-only.
- Keep functions within the 2–3 parameter rule.
- Use `asyncio.sleep` for backoff (never block the event loop) and `asyncio.wait_for` for the per-attempt timeout.
- Logging via `src.lib.utils.logging.logger` only — never `print()`.
- Verify each test fails before implementing; commit after each task or logical group.

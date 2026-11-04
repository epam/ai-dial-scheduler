---

description: "Task list for feature 039: Split Run Execution Timeouts"
---

# Tasks: Split Run Execution Timeouts

**Input**: Design documents from `/specs/039-split-run-timeouts/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/schedule-properties.md, quickstart.md

**Tests**: Included. Constitution III (Test-First) is NON-NEGOTIABLE: every test task MUST be written and confirmed
FAILING before the implementation task it precedes.

**Organization**: Tasks are grouped by user story so each story can be implemented and tested independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Include exact file paths in descriptions

## Path Conventions

Single project: shared code in `src/lib/`, API in `src/api/`, worker in `src/worker/`; tests mirror it under
`tests/unit/` and `tests/integration/`. `src/lib/` MUST NOT import from `src.api` or `src.worker`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish a green baseline. No new dependencies and no migration are introduced by this feature.

- [x] T001 Run the quality gates on the starting branch (`uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest`) and record the baseline pass count, so later regressions are attributable

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The settings, schema field, context model and resolution logic that every story reads. No story can be
verified until the orchestrator resolves both bounds onto `RunContext`.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests first (confirm FAILING) ⚠️

- [x] T002 [P] In `tests/unit/lib/test_settings.py`, assert on both `ApiHttpSettings` and `WorkerHttpSettings` that `max_duration_seconds == 600.0`, `streaming_max_duration_seconds == 3600.0` and `idle_timeout_seconds == 60.0` by default; that each honours its env var (`API_HTTP_MAX_DURATION_SECONDS`, `API_HTTP_STREAMING_MAX_DURATION_SECONDS`, `API_HTTP_IDLE_TIMEOUT_SECONDS` and the `WORKER_HTTP_*` equivalents); and that `streaming_max_duration_seconds > max_duration_seconds`
- [x] T003 [P] In `tests/unit/lib/schemas/test_schedule_schemas.py` (`TestScheduleIdleTimeout`), assert `idle_timeout` defaults to `None`; a positive value is accepted; `0` and negative values are rejected ("`> 0` when set"); and `is_streaming` is `True` for `sse`, follows `payload.stream` for `chat_completion`/`responses`, and is `False` for `rest`
- [x] T004 [P] In `tests/unit/worker/test_retry.py` (`TestCeilingResolution`, `TestIdleTimeoutResolution`, `TestEffectiveTimeoutThreading`), assert the precedence `properties.timeout` → `ctx.timeouts` → `worker_http` settings for the ceiling and `properties.idle_timeout` → `ctx.timeouts.idle_timeout` → `worker_http.idle_timeout_seconds` for the idle bound; that a streaming target defaults to `streaming_max_duration` and a non-streaming one to `max_duration`; that both resolved values land on `ctx.effective_timeout` / `ctx.effective_idle_timeout`; and that both appear in the `attempt_start` log entry
- [x] T005 [P] In `tests/unit/lib/services/test_run_launcher.py`, assert that `BackgroundRunLauncher(http_client, timeouts=RunTimeouts(...))` threads the API process's `RunTimeouts` (all three values) and its own `http_client` onto the `RunContext` passed to `execute_run`

### Implementation

- [x] T006 [P] Add `max_duration_seconds: float = 600.0`, `streaming_max_duration_seconds: float = 3600.0` and `idle_timeout_seconds: float = 60.0` to both `ApiHttpSettings` (`env_prefix="API_HTTP_"`) and `WorkerHttpSettings` (`env_prefix="WORKER_HTTP_"`) in `src/lib/settings.py`, with a short comment explaining why the streaming ceiling is longer (research.md R3, R5)
- [x] T007 [P] Add `idle_timeout: float | None = None` to `ScheduleProperties` in `src/lib/schemas/schedule.py` with a field validator mirroring `validate_timeout` (reject non-positive values with a message naming the field and its unit), and add the read-only `is_streaming` property: `True` for `sse`, `bool(payload.get("stream"))` for `chat_completion`/`responses`, `False` for `rest` (data-model.md "`ScheduleProperties`")
- [x] T008 [P] Add the `RunTimeouts` model (`max_duration: float`, `streaming_max_duration: float`, `idle_timeout: float`) to `src/lib/schemas/run.py`, and add `timeouts: RunTimeouts | None = None` and `effective_idle_timeout: float | None = None` to `RunContext`; document that `None` means "fall back to this process's `WORKER_HTTP_*` settings" (data-model.md "`RunContext`")
- [x] T009 In `src/worker/retry.py` (`execute_with_policy`), resolve once per attempt: `defaults = ctx.timeouts or RunTimeouts(from get_settings().worker_http)`, `ceiling_default = defaults.streaming_max_duration if properties.is_streaming else defaults.max_duration`, `ctx.effective_timeout = properties.timeout ?? ceiling_default`, `ctx.effective_idle_timeout = properties.idle_timeout ?? defaults.idle_timeout`; keep `asyncio.wait_for` on the ceiling; add both values to the `attempt_start` payload (depends on T006–T008; data-model.md "Resolution order")
- [x] T010 Thread the API process's bounds: `BackgroundRunLauncher.__init__(self, http_client, timeouts: RunTimeouts)` in `src/lib/services/run_launcher.py` sets `RunContext.timeouts`, and `src/api/app.py` builds `RunTimeouts` from `api_http.max_duration_seconds`, `api_http.streaming_max_duration_seconds` and `api_http.idle_timeout_seconds` (depends on T008; FR-005)

**Checkpoint**: Both bounds are configurable per process and resolved onto `RunContext` for every attempt.

---

## Phase 3: User Story 1 - A long but healthy run completes (Priority: P1) 🎯 MVP

**Goal**: A streaming target that keeps emitting events finishes, even when its total duration exceeds the idle
bound by far, as long as no single gap exceeds it and it ends within the ceiling.

**Independent Test**: A streaming executor fed events every few hundred milliseconds for longer than
`effective_idle_timeout` returns a result and no error (quickstart.md "A healthy long stream completes").

### Tests for User Story 1 (confirm FAILING) ⚠️

- [x] T011 [P] [US1] In `tests/unit/worker/executors/test_base.py` (`TestIterateWithIdleTimeout`), assert `iterate_with_idle_timeout` yields every item within the bound, that total duration may exceed the bound, that it passes items through unchanged when the bound is `None`, and that an empty stream terminates
- [x] T012 [P] [US1] In `tests/unit/worker/executors/test_sse.py` (`TestSseExecutorIdleTimeout`), add `test_healthy_long_stream_is_not_cut_off`: events spaced under `effective_idle_timeout` for a total longer than it complete successfully; and `test_no_idle_bound_leaves_the_stream_unbounded`
- [x] T013 [P] [US1] In `tests/unit/worker/executors/test_chat_completion.py` and `tests/unit/worker/executors/test_responses.py`, assert that `payload.stream: true` uses the streaming SDK call wrapped by the idle iterator and assembles the same result shape as the non-streaming `.create()` path

### Implementation for User Story 1

- [x] T014 [US1] Add `iterate_with_idle_timeout(iterator, idle_timeout, ctx)` to `src/worker/executors/base.py`: wrap each `anext()` in `asyncio.timeout(idle_timeout)`, stop on `StopAsyncIteration`, and pass through untouched when `idle_timeout is None` (research.md R2)
- [x] T015 [P] [US1] In `src/worker/executors/sse.py`, iterate `event_source.aiter_sse()` through `iterate_with_idle_timeout(..., ctx.effective_idle_timeout, ctx)` (depends on T014)
- [x] T016 [P] [US1] In `src/worker/executors/chat_completion.py`, branch on whether the request streams: on the streaming branch iterate the SDK stream through `iterate_with_idle_timeout`; leave the non-streaming `.create()` branch without an idle bound (FR-007; depends on T014)
- [x] T017 [P] [US1] In `src/worker/executors/responses.py`, apply the same streaming / non-streaming branch as T016 (depends on T014)

**Checkpoint**: The incident scenario (SC-001, SC-002) passes in unit tests; a healthy stream is no longer cut at a fixed bound.

---

## Phase 4: User Story 2 - A genuinely hung run is abandoned (Priority: P1)

**Goal**: A stream that delivers no parsed event for longer than the idle bound is abandoned, even while heartbeat
pings keep the transport alive. The failure is recorded with a reason naming stream inactivity, distinct from a
ceiling expiry.

**Independent Test**: An SSE fixture that sends only `:` comment lines after the first event fails at
`effective_idle_timeout`, logs `attempt_idle_timed_out`, and is not retried (quickstart.md "A heartbeating but hung
stream is abandoned").

### Tests for User Story 2 (confirm FAILING) ⚠️

- [x] T018 [P] [US2] In `tests/unit/worker/executors/test_base.py` (`TestIterateWithIdleTimeout`), assert a single gap exceeding the bound raises `StreamIdleTimeoutError` carrying `run_id`/`schedule_id` and `status_code is None`, and that a `TimeoutError` raised by the wrapped iterator itself is NOT relabelled as an idle expiry
- [x] T019 [P] [US2] In `tests/unit/worker/executors/test_sse.py` (`TestSseExecutorIdleTimeout`), add `test_stalled_stream_fails_at_the_idle_bound` using a fixture that emits SSE comment (heartbeat) lines, not events, after the stall; confirm it would also fail against an httpx-read-timeout implementation (SC-003)
- [x] T020 [P] [US2] In `tests/unit/worker/test_retry.py` (`TestIdleTimeoutReporting`), assert an executor raising `StreamIdleTimeoutError` produces exactly one `attempt_idle_timed_out` log entry, no `attempt_timed_out`, no retry even when the retry policy lists every status code, and a persisted failure reason containing `stream produced no event for {n}s`

### Implementation for User Story 2

- [x] T021 [US2] Add `StreamIdleTimeoutError(ScheduleRunError)` to `src/lib/exceptions/errors.py` with message `stream produced no event for {n}s` and no status code (data-model.md "Failure modes")
- [x] T022 [US2] In `iterate_with_idle_timeout` (`src/worker/executors/base.py`), convert the idle `asyncio.timeout` expiry, and only that expiry, into `StreamIdleTimeoutError` (depends on T014, T021)
- [x] T023 [US2] In `src/worker/retry.py`, catch `StreamIdleTimeoutError` before the generic `ScheduleRunError` handling and log it as `attempt_idle_timed_out` (new `IDLE_TIMED_OUT` event constant beside `TIMED_OUT = "attempt_timed_out"`), returning terminally without consulting the retry policy (FR-011, research.md R9; depends on T021)

**Checkpoint**: Stall detection works on a heartbeating target, and the two expiries are distinguishable by event name alone (SC-003, SC-006).

---

## Phase 5: User Story 3 - An unreachable host fails fast (Priority: P2)

**Goal**: An attempt against a host that refuses connections fails at the configured connect timeout (5 s by
default), not at the ceiling.

**Independent Test**: For every executor, the per-request `httpx.Timeout` has `read == effective_timeout` and
`connect == 5.0` (the client's configured value), not `connect == effective_timeout`.

### Tests for User Story 3 (confirm FAILING) ⚠️

- [x] T024 [P] [US3] In `tests/unit/worker/executors/test_base.py` (`TestBuildTimeout`), assert `build_timeout(client, None)` returns `None` and `build_timeout(client, x)` overrides only `read`, copying `connect`, `write` and `pool` from `client.timeout`
- [x] T025 [P] [US3] In `tests/unit/worker/executors/test_rest.py` and `tests/unit/worker/executors/test_sse.py` (`Test*ExecutorTimeout`), add `test_connect_phase_is_preserved`: the request timeout has `read == effective_timeout` and `connect == 5.0`; and assert no `timeout` kwarg is passed when `effective_timeout` is `None`
- [x] T026 [P] [US3] In `tests/unit/worker/executors/test_chat_completion.py` and `tests/unit/worker/executors/test_responses.py` (`Test*ExecutorTimeout`), assert the SDK receives an `httpx.Timeout` with the connect phase preserved, never a bare float

### Implementation for User Story 3

- [x] T027 [US3] Add `build_timeout(client: httpx.AsyncClient, read_seconds: float | None) -> httpx.Timeout | None` to `src/worker/executors/base.py`: return `None` for `None`, otherwise `httpx.Timeout(connect=client.timeout.connect, read=read_seconds, write=client.timeout.write, pool=client.timeout.pool)` (research.md R7)
- [x] T028 [P] [US3] Replace `httpx.Timeout(ctx.effective_timeout)` with `build_timeout(client, ctx.effective_timeout)` in `src/worker/executors/rest.py` (depends on T027)
- [x] T029 [P] [US3] Replace the single-positional `httpx.Timeout(...)` with `build_timeout(...)` in `src/worker/executors/sse.py` (depends on T027)
- [x] T030 [P] [US3] Pass `build_timeout(http_client, ctx.effective_timeout)` instead of a bare float to the SDK in `src/worker/executors/chat_completion.py` and `src/worker/executors/responses.py` (depends on T027)

**Checkpoint**: An unreachable host fails within the connect timeout for all four target types (SC-005).

---

## Phase 6: User Story 4 - An operator tunes a single schedule (Priority: P3)

**Goal**: Both bounds are settable per schedule, echoed back on read, survive to the worker, and an invalid pairing
is rejected at create/update time.

**Independent Test**: `POST /v1/schedules` with `timeout: 60, idle_timeout: 120` returns 422; with
`idle_timeout: 90` alone it succeeds and `GET` echoes `idle_timeout: 90`.

### Tests for User Story 4 (confirm FAILING) ⚠️

- [x] T031 [P] [US4] In `tests/unit/lib/schemas/test_schedule_schemas.py` (`TestScheduleIdleTimeout`), assert `idle_timeout > timeout` is rejected only when both are explicitly set; `idle_timeout` alone is accepted; `idle_timeout == timeout` is accepted; `idle_timeout` on a non-streaming target is accepted and inert; and a JSON round-trip (`model_dump` → `ScheduleProperties(**kwargs)`) preserves both fields (FR-009, FR-012)
- [x] T032 [P] [US4] In `tests/integration/test_schedule_run_now.py`, assert a schedule created with `idle_timeout` keeps it in the run's `schedule_snapshot`

### Implementation for User Story 4

- [x] T033 [US4] Add the `validate_timeout_bounds` model validator to `ScheduleProperties` in `src/lib/schemas/schedule.py`: reject `idle_timeout > timeout` "**only when both are explicitly set**", with a message naming both fields; give `timeout` and `idle_timeout` one-line `Field(description=...)` strings that distinguish ceiling from gap-between-events and name the unit (contracts/schedule-properties.md "Field rules")
- [x] T034 [P] [US4] Add `"timeout": 1800` and `"idle_timeout": 60` to the streaming entries of `_SCHEDULE_EXAMPLES` in `src/api/v1/endpoints/schedules.py`, with a short comment, so OpenAPI and MCP tool guidance show the distinction (contracts/schedule-properties.md "MCP surface")

**Checkpoint**: Per-schedule tuning works end to end through the existing `properties` contract, with no migration (SC-008).

---

## Phase 7: Remove `*_HTTP_TIMEOUT_SECONDS` (FR-014)

**Purpose**: After the split, `timeout_seconds` bounds no run. Remove it so DIAL metadata calls use the shared
client's per-phase timeouts instead of a flat value (research.md R11). Depends on Phases 2–6.

### Tests first (confirm FAILING) ⚠️

- [x] T035 [P] In `tests/unit/lib/test_settings.py`, add `test_removed_timeout_seconds_env_var_is_ignored`: with `WORKER_HTTP_TIMEOUT_SECONDS` set, `WorkerHttpSettings()` constructs, has no `timeout_seconds` attribute, and keeps `max_duration_seconds == 600.0`; remove `timeout_seconds` assertions from the default and env-override tests
- [x] T036 [P] In `tests/unit/lib/clients/test_dial_client.py`, construct `DialClient(http_client, base_url)` without a timeout and assert requests pass no `timeout` kwarg (replacing the tests that asserted a passed-through timeout)

### Implementation

- [x] T037 Remove `timeout_seconds` from `ApiHttpSettings` and `WorkerHttpSettings` in `src/lib/settings.py`
- [x] T038 Remove the `timeout` constructor parameter and every `timeout=self._timeout` request kwarg from `DialClient` in `src/lib/clients/dial_client.py`; note in the class docstring that requests inherit the client's per-phase timeouts; leave `request_external_service_signin`'s own `timeout` argument unchanged
- [x] T039 Remove `RunContext.default_timeout` from `src/lib/schemas/run.py` and the `default_timeout` parameter from `BackgroundRunLauncher.__init__` in `src/lib/services/run_launcher.py`
- [x] T040 Update every caller: `DialClient(...)` without a timeout in `src/api/dependencies/auth.py`, `src/worker/jobs.py` and `src/worker/conversation.py`; drop `default_timeout=` from `src/api/app.py`; drop the ignored positional default from `httpx.Timeout(...)` in `src/api/app.py`, `src/worker/clients.py` and `src/api/mcp/backing_client.py`; update test fixtures that still set `timeout_seconds` or `default_timeout` (`tests/unit/api/mcp/test_mcp_tools.py`, `tests/unit/api/mcp/test_server.py`, `tests/unit/worker/test_jobs.py`, `tests/unit/worker/test_job_spans.py`, `tests/unit/worker/test_conversation.py`, `tests/unit/api/dependencies/test_sign_in.py`, `tests/integration/test_schedule_run_now.py`)

**Checkpoint**: `grep -rn "timeout_seconds\b\|default_timeout" src` returns nothing related to HTTP settings.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, changelog and end-to-end validation.

- [x] T041 [P] Document `*_HTTP_MAX_DURATION_SECONDS` (600.0), `*_HTTP_STREAMING_MAX_DURATION_SECONDS` (3600.0) and `*_HTTP_IDLE_TIMEOUT_SECONDS` (60.0) in both the API and Worker env tables of `README.md`, and remove the `*_HTTP_TIMEOUT_SECONDS` rows
- [x] T042 [P] Describe `timeout` and `idle_timeout` semantics, defaults and the two log events in `documentation/properties.md` (preserve the file's CRLF line endings)
- [x] T043 [P] Add an `[Unreleased]` entry to `CHANGELOG.md` covering `idle_timeout`, the six new env vars, the 600 s / 3600 s ceiling defaults, the restored connect phase, and the removal of `API_HTTP_TIMEOUT_SECONDS` / `WORKER_HTTP_TIMEOUT_SECONDS` (Constitution, Development Workflow)
- [x] T044 Run the quickstart.md quality gates (`ruff check`, `ruff format --check`, `mypy src`, `pytest --cov=src`) and `uv run alembic check` (must report nothing pending, SC-008)
- [x] T045 Perform the quickstart.md manual verification against the affected production schedule, confirm a persisted success (SC-001), and record the wall-clock duration in plan.md's open item on the unmeasured streaming ceiling

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **US1 (Phase 3)** and **US3 (Phase 5)**: Depend on Foundational
- **US2 (Phase 4)**: Depends on US1 (T022 extends the `iterate_with_idle_timeout` created in T014)
- **US4 (Phase 6)**: Depends on Foundational only (T033 extends the schema field from T007)
- **FR-014 removal (Phase 7)**: Depends on Phases 2–6 (T039/T040 edit files those phases touch)
- **Polish (Phase 8)**: Depends on all previous phases

### User Story Dependencies

- **US1 (P1)**: Independent after Foundational
- **US2 (P1)**: Builds on US1's idle iterator; independently testable via the heartbeat fixture
- **US3 (P2)**: Independent after Foundational. Shares `src/worker/executors/base.py` and the four executor files with US1/US2, so run it sequentially with them rather than in parallel
- **US4 (P3)**: Independent after Foundational

### Within Each User Story

- Tests MUST be written and FAIL before implementation (Constitution III)
- Shared helper in `base.py` before the executors that call it
- Exception type (T021) before the code that raises (T022) or catches (T023) it

### Parallel Opportunities

- Phase 2: T002–T005 (four test files) in parallel; then T006, T007, T008 (three source files) in parallel
- US1: T011–T013 in parallel; then T015–T017 in parallel after T014
- US2: T018–T020 in parallel
- US3: T024–T026 in parallel; then T028–T030 in parallel after T027
- US4: T031, T032 in parallel; T034 in parallel with T033
- Phase 7: T035, T036 in parallel
- Phase 8: T041–T043 in parallel

---

## Parallel Example: User Story 1

```bash
# Tests first, together:
Task: "TestIterateWithIdleTimeout healthy-path cases in tests/unit/worker/executors/test_base.py"
Task: "test_healthy_long_stream_is_not_cut_off in tests/unit/worker/executors/test_sse.py"
Task: "Streaming-branch tests in tests/unit/worker/executors/test_chat_completion.py and test_responses.py"

# After T014 (iterate_with_idle_timeout in base.py), the three executors together:
Task: "Wrap aiter_sse() in src/worker/executors/sse.py"
Task: "Streaming branch in src/worker/executors/chat_completion.py"
Task: "Streaming branch in src/worker/executors/responses.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: the healthy-long-stream tests pass; the incident schedule no longer fails at a fixed bound
5. Do **not** ship US1 alone to production: without US2 a hung streaming run is bounded only by the 3600 s ceiling. US1 + US2 together are the minimum releasable increment

### Incremental Delivery

1. Setup + Foundational → bounds resolved per process
2. US1 + US2 → healthy runs complete, hung runs are abandoned (release candidate)
3. US3 → unreachable hosts fail in 5 s
4. US4 → per-schedule tuning and validation
5. Phase 7 → `timeout_seconds` removed
6. Polish → docs, changelog, live verification

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps a task to its user story for traceability
- Commit after each task or logical group
- `documentation/properties.md`, `tests/unit/lib/clients/test_dial_client.py` and `tests/unit/worker/test_job_spans.py` use CRLF line endings; preserve them when editing

---

## Phase 9: Convergence

- [x] T046 [P] Add a heartbeat test in `tests/unit/worker/executors/test_sse.py` that drives the real `httpx_sse` parser through an `httpx.AsyncClient(transport=httpx.MockTransport(...))` with a streamed body of one `data:` event followed only by `: ping` comment lines spaced under `effective_idle_timeout`, and assert `SseExecutor().execute` raises `StreamIdleTimeoutError` at the idle bound rather than completing or waiting for the ceiling per SC-003 (partial)
- [x] T047 [P] Add idle tests to `tests/unit/worker/executors/test_chat_completion.py` and `tests/unit/worker/executors/test_responses.py`: with `payload.stream: true`, a stream whose events are spaced under `effective_idle_timeout` for a total longer than it completes, and a stream that stalls raises `StreamIdleTimeoutError` itself (not a plain `ScheduleRunError`), proving it escapes the executor's narrow `except (OpenAIError, httpx.TransportError, TypeError, ValueError)` per US2/AC1 (partial)
- [x] T048 [P] Add an `[Unreleased]` entry to `CHANGELOG.md` covering the new `idle_timeout` schedule property, the six `*_HTTP_MAX_DURATION_SECONDS` / `*_HTTP_STREAMING_MAX_DURATION_SECONDS` / `*_HTTP_IDLE_TIMEOUT_SECONDS` env vars with their 600 / 3600 / 60 s defaults, the restored per-phase connect timeout, the `attempt_idle_timed_out` log event, and the removal of `API_HTTP_TIMEOUT_SECONDS` / `WORKER_HTTP_TIMEOUT_SECONDS` per Constitution: Development Workflow (missing)
- [x] T049 [P] Add a parametrised `is_streaming` test to `tests/unit/lib/schemas/test_schedule_schemas.py` covering `sse` → `True`, `rest` → `False`, and `chat_completion` / `responses` with `payload.stream` `true`, `false` and absent (absent must be `False` via the `validate_target_rules` injection) per T003 (partial)
- [x] T050 [P] Add env-override assertions for `API_HTTP_STREAMING_MAX_DURATION_SECONDS` and `WORKER_HTTP_STREAMING_MAX_DURATION_SECONDS` to `tests/unit/lib/test_settings.py` per FR-005 (partial)
- [x] T051 [P] Assert in `tests/integration/test_schedule_run_now.py` that a schedule created with `idle_timeout` carries it unchanged into the run's `schedule_snapshot` per FR-012 (partial)
- [x] T052 [P] Add a test in `tests/unit/worker/test_jobs.py` that a run whose executor raises `StreamIdleTimeoutError` is persisted with `error_text` containing `stream produced no event for`, distinct from a ceiling expiry's `execution timed out after`, per FR-011 (partial)

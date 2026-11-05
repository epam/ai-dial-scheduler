# Tasks: Responses Schedule Type

**Input**: Design documents from `/specs/028-responses-schedule-type/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — the project constitution (Principle III, Test-First NON-NEGOTIABLE) mandates TDD. Tests are written and confirmed failing before implementation.

**Organization**: Tasks are grouped by user story. Because this feature is a single additive execution path, the shared core (enum value + validation branch + executor + dispatcher registration) lives in the Foundational phase; each user story phase adds the tests that prove its specific behavior.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1, US2, US3 — maps to spec.md user stories
- Exact file paths included in each task

## Path Conventions

Web service layout per constitution v1.2.0: `src/api/`, `src/worker/`, shared `src/lib/`; tests under `tests/unit/`, `tests/integration/`, `tests/e2e/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the working environment; no new project scaffolding is required (additive feature).

- [X] T001 Verify dev environment: run `uv sync`, then confirm baseline green with `uv run pytest`, `uv run ruff check src tests`, and `uv run mypy src` before making changes.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared execution path that ALL user stories depend on — the new target type, its validation, the executor, and dispatcher wiring.

**⚠️ CRITICAL**: No user story is testable until this phase is complete.

### Tests First (write and confirm FAILING before implementation)

- [X] T002 [P] Add failing unit tests for `responses` validation in `tests/unit/lib/schemas/test_schedule_schemas.py`: (a) valid `responses` props accepted with `stream` defaulted to `False`; (b) `method` set → `ValueError`; (c) missing/empty `api_version` → `ValueError`; (d) `create_conversation=True` → `ValueError` (chat-completion-only).
- [X] T003 [P] Add failing unit test for dispatcher resolution in `tests/unit/worker/test_dispatcher.py`: `resolve_executor(TargetType.RESPONSES)` returns a `ResponsesExecutor` instance.
- [X] T004 [P] Add failing unit tests for the executor in `tests/unit/worker/executors/test_responses.py`: (a) non-streaming path returns `{"result": <Response.model_dump()>}`; (b) streaming path assembles a single final Response via `get_final_response()` and returns the same shape; (c) `stream` flag popped from create kwargs and `extra_headers` forwarded; (d) `effective_timeout` applied when set; (e) `APIStatusError` → `ScheduleRunError` with `status_code`; (f) other `Exception` → `ScheduleRunError` without status code. Use `pytest-mock` to stub `AsyncAzureOpenAI.responses`.

### Implementation

- [X] T005 Add `RESPONSES = "responses"` to the `TargetType` enum in `src/lib/db/enums.py`.
- [X] T006 Extend `ScheduleProperties.validate_target_rules` in `src/lib/schemas/schedule.py` so `target_type == TargetType.RESPONSES` follows the same branch as `CHAT_COMPLETION` (no `method`; `api_version` required; default `payload["stream"]=False`), while keeping `create_conversation` allowed only for `CHAT_COMPLETION` (rejected for `responses`, `rest`, `sse`). (Makes T002 pass.)
- [X] T007 Create `ResponsesExecutor` in `src/worker/executors/responses.py` implementing the `Executor` protocol per `contracts/responses-executor.md`: strip `Bearer` from OBO api-key, parse deployment from URL, build `AsyncAzureOpenAI` with `HttpClientProvider().client`, call `client.responses.stream(**kwargs)` + `get_final_response()` (logging stream events by `type` only), return `{"result": response.model_dump()}`, and map `APIStatusError`/`Exception` to `ScheduleRunError`. (Makes T004 pass.)
- [X] T008 Register `TargetType.RESPONSES: ResponsesExecutor()` in `_EXECUTOR_MAP` in `src/worker/dispatcher.py` and add the import. (Makes T003 pass.)
- [X] T009 Refactor the deployment-URL parser so both executors share one implementation: extract `_parse_deployment` into a shared helper in `src/lib/utils/dial.py` (or reuse the existing `extract_deployment_id`), and have both `src/worker/executors/chat_completion.py` and `src/worker/executors/responses.py` use it — avoiding duplicated parsing logic (DRY).

**Checkpoint**: Enum, validation, executor, and dispatcher exist; T002–T004 pass. User story verification can begin.

---

## Phase 3: User Story 1 - Schedule a recurring Responses-API call (Priority: P1) 🎯 MVP

**Goal**: A user can create a `responses` schedule; each run invokes the deployment's Responses API and stores the assembled response as the run result.

**Independent Test**: Create a `responses` schedule against a valid deployment, trigger a run, and confirm it completes with the assembled Response stored under `result`.

### Tests for User Story 1 ⚠️

- [X] T010 [P] [US1] Add integration test in `tests/integration/test_schedules_api.py` (or a new `tests/integration/test_responses_schedule.py`) creating a schedule with `target_type: "responses"` and asserting the response echoes `properties.target_type == "responses"`; assert negative cases (`method` set → 422, missing `api_version` → 422, `create_conversation: true` → 422).

### Implementation for User Story 1

- [X] T011 [US1] Verify the create/run path end-to-end for `responses` (no code change expected beyond Foundational): confirm `jobs.py::_execute_scheduled_job_inner` builds `ScheduleProperties`, resolves the executor, and persists success with the assembled result; adjust only if a `responses`-specific gap surfaces.
- [X] T012 [P] [US1] Add an e2e request example `tests/e2e/*.http` for creating a `responses` schedule (mirror the existing `1.1/1.2 Create-chat-completion-schedule*.http` files), using a `/openai/deployments/{deployment}/responses` URL.
- [X] T013 [US1] Update `documentation/properties.md`: add `responses` to the `target_type` enum list and note it uses the Responses API (single `input`/`instructions` request) while sharing chat-completion validation; keep `create_conversation` documented as chat-completion-only.

**Checkpoint**: User Story 1 fully functional — `responses` schedules can be created, run, and store results.

---

## Phase 4: User Story 2 - Consistent behavior with chat completion (Priority: P2)

**Goal**: `responses` schedules reuse triggering, timeout, retry, run tracking, pause/resume, and OBO authorization identically to chat completion.

**Independent Test**: Configure a `responses` schedule with timeout + retry + metadata and confirm each behaves identically to an equivalent chat completion schedule.

### Tests for User Story 2 ⚠️

- [X] T014 [P] [US2] Add unit tests in `tests/unit/worker/executors/test_responses.py` (or `tests/unit/worker/test_retry.py` if colocated) asserting timeout enforcement: an executor call exceeding `effective_timeout` yields a timeout `RunOutcome` via `execute_with_policy` (parity with chat completion).
- [X] T015 [P] [US2] Add unit test asserting retry eligibility: a `ResponsesExecutor` `APIStatusError` with a retryable status code is retried by `execute_with_policy` per `interval_sequence`, and a non-retryable/status-less error is terminal.

### Implementation for User Story 2

- [X] T016 [US2] Confirm no code changes are needed for parity (retry/timeout/tracking/pause-resume are target-type-agnostic in `src/worker/retry.py` and `src/worker/jobs.py`); if any branch special-cases `CHAT_COMPLETION` where `responses` should behave identically, generalize it. Verify OBO credential resolution path is unchanged for `responses`.

**Checkpoint**: User Stories 1 AND 2 both verified — behavior parity confirmed by tests.

---

## Phase 5: User Story 3 - Streaming and non-streaming produce the same stored result (Priority: P3)

**Goal**: Streaming and non-streaming `responses` runs store a structurally equivalent assembled Response.

**Independent Test**: Run one streaming and one non-streaming `responses` schedule and confirm both store an equivalent assembled result shape.

### Tests for User Story 3 ⚠️

- [X] T017 [P] [US3] Add/verify unit tests in `tests/unit/worker/executors/test_responses.py` asserting the stored `result` shape is identical for `stream: true` and `stream: false` payloads (both an assembled `Response.model_dump()` under `result`).

### Implementation for User Story 3

- [X] T018 [US3] Confirm the executor produces one assembled Response regardless of the `stream` flag (already implemented in T007 via `responses.stream()` + `get_final_response()`); adjust only if T017 reveals a shape divergence between paths.

**Checkpoint**: All three user stories independently verified.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates and final validation across the feature.

- [X] T019 Run `uv run ruff check src tests` and `uv run ruff format src tests`; fix any issues not auto-fixed.
- [X] T020 Run `uv run mypy src` and resolve any type errors introduced by the new executor/enum/validation.
- [X] T021 Run the full suite `uv run pytest --cov=src` and ensure all new code is covered and green.
- [ ] T022 Execute `specs/028-responses-schedule-type/quickstart.md` scenarios 1–4 against a running stack to validate end-to-end behavior.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories. This is the bulk of the implementation.
- **User Stories (Phase 3–5)**: All depend on Foundational. Given the shared core, US1 delivers the MVP; US2 and US3 are primarily verification/parity layers over the same executor.
- **Polish (Phase 6)**: Depends on all targeted stories being complete.

### User Story Dependencies

- **US1 (P1)**: Depends only on Foundational. Delivers the demonstrable slice.
- **US2 (P2)**: Depends on Foundational; verifies reused orchestration. Independently testable.
- **US3 (P3)**: Depends on Foundational; verifies result-shape parity. Independently testable.

### Within Each Phase

- Tests are written and confirmed FAILING before implementation (TDD).
- Foundational: enum (T005) → validation (T006); executor (T007) → dispatcher registration (T008); parser refactor (T009) touches both executors so sequence after T007.

### Parallel Opportunities

- Foundational tests T002, T003, T004 are in different files → run in parallel.
- T005 (enums) and the test-authoring tasks are independent files → parallelizable.
- US1 e2e example (T012) and docs (T013) are different files → parallel.
- US2 tests T014/T015 and US3 test T017 target the same test module — sequence edits to `test_responses.py` to avoid conflicts (not [P] against each other if same file).

---

## Parallel Example: Foundational Tests

```bash
# Author these failing tests together (different files):
Task: "responses validation tests in tests/unit/lib/schemas/test_schedule_schemas.py"
Task: "dispatcher resolution test in tests/unit/worker/test_dispatcher.py"
Task: "ResponsesExecutor tests in tests/unit/worker/executors/test_responses.py"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1: Setup (baseline green).
2. Phase 2: Foundational — enum, validation, executor, dispatcher (with TDD).
3. Phase 3: User Story 1 — create + run + store, e2e example, docs.
4. **STOP and VALIDATE**: create a `responses` schedule and confirm a run stores an assembled result.

### Incremental Delivery

1. Foundational → core ready.
2. US1 → MVP (create/run/store).
3. US2 → parity verified (timeout/retry/tracking).
4. US3 → streaming/non-streaming parity verified.
5. Polish → all quality gates green + quickstart validated.

---

## Notes

- [P] = different files, no dependencies.
- This feature is additive: no DB migration (target type/props live in JSONB `kwargs`), no `api/` code changes (enum auto-reflected in OpenAPI).
- Keep `create_conversation` chat-completion-only; `responses` conversation creation is explicitly out of scope.
- Never log streamed content, prompt/response text, or credentials — event `type` only (FR-010).
- Commit after each task or logical group.

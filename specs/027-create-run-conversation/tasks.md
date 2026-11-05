---
description: "Task list for Create DIAL Conversation on Run Complete"
---

# Tasks: Create DIAL Conversation on Run Complete

**Input**: Design documents from `/specs/027-create-run-conversation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED — Constitution Principle III (Test-First, NON-NEGOTIABLE) mandates TDD. Every implementation task is preceded by failing tests.

**Organization**: Tasks are grouped by user story (US1–US4) to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1, US2, US3, US4 (maps to spec.md user stories)
- Exact file paths are included in each task

## Path Conventions

Web service layout (constitution v1.2.0): `src/api`, `src/worker`, shared `src/lib`; tests mirror under `tests/`. No DB migration (flag rides `ScheduleProperties` JSONB).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Constants and typed models shared by all stories. No project init needed (existing repo).

- [x] T001 Add DIAL path constants to `src/lib/constants.py`: `BUCKET_PATH = "/v1/bucket"`, `APPLICATION_DETAILS_PATH_TEMPLATE = "/openai/applications/{application_id}"`, `CONVERSATION_PATH_TEMPLATE = "/v1/conversations/{bucket}/{conversation_path}"`, `SCHEDULER_CONVERSATION_FOLDER = ".scheduler"`, and `APPLICATION_DEPLOYMENT_PREFIX = "applications/"`.
- [x] T002 [P] Add `ConversationCreationError` to `src/lib/exceptions/errors.py` (worker-internal, non-fatal; carries message + optional cause; never secrets/streamed content), following the existing exception hierarchy pattern.

**Checkpoint**: Shared constants and error type available.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Pydantic conversation schemas and DIAL client methods that ALL conversation-creating stories depend on.

**⚠️ CRITICAL**: No conversation-creation story (US1–US3) can begin until this phase is complete.

### Tests for Foundational (write first, ensure they FAIL) ⚠️

- [x] T003 [P] Unit tests for conversation schemas in `tests/lib/schemas/test_conversation.py`: `DialModel`, `DialMessage` (user & assistant, custom_content optional), `DialConversation` (2-message shape, `id`/`folderId` format), and `ApplicationDetails` (parses `reference`, ignores extras; missing `reference` invalid). Assert shapes against `conversations/gpt-5-6/dial_conversation.json` and `conversations/app/dial_conversation.json`.
- [x] T004 [P] Unit tests for new `DialClient` methods in `tests/lib/clients/test_dial_client.py` with mocked httpx: `get_bucket` (200 → bucket string; non-2xx/transport/missing-key → `ConversationCreationError`), `get_application` (200 → `ApplicationDetails.reference`; error paths → `ConversationCreationError`), `put_conversation` (2xx → None; non-2xx/transport → `ConversationCreationError`; asserts URL path `/v1/conversations/{bucket}/.scheduler/{schedule_id}/{run_id}` and Api-Key header).

### Implementation for Foundational

- [x] T005 [P] Create conversation schemas in `src/lib/schemas/conversation.py`: `DialModel`, `DialMessage`, `DialConversation`, `ApplicationDetails` per data-model.md (Pydantic v2, type hints, Google-style docstrings).
- [x] T006 Add `get_bucket`, `get_application`, `put_conversation` to `src/lib/clients/dial_client.py` per `contracts/dial-client.md` (reuse shared httpx client + timeout; raise `ConversationCreationError`; URL-encode application id and path segments; never log token/content). Depends on T001, T002, T005.

**Checkpoint**: Conversation models + DIAL access verified by unit tests. US1–US3 can now proceed.

---

## Phase 3: User Story 1 - Opt in to conversation creation (Priority: P1) 🎯 MVP

**Goal**: Chat completion schedules carry an opt-in `create_conversation` flag (default false, rejected for non-chat-completion); successful opted-in runs produce a 2-message DIAL conversation stored at `conversations/{bucket}/.scheduler/{schedule_id}/{run_id}`.

**Independent Test**: Create a chat_completion schedule with `create_conversation=true` targeting a **model** deployment, let a run succeed, and confirm a conversation with the user prompt + assistant response exists at the run-specific path; with the flag off/absent, none is created.

### Tests for User Story 1 (write first, ensure they FAIL) ⚠️

- [x] T007 [P] [US1] Unit tests in `tests/lib/schemas/test_schedule.py`: `create_conversation` defaults to `False`; accepted when `target_type=chat_completion`; raises `ValueError` for `rest`/`sse` when `True`; accepted (no-op) when `False` for any target.
- [x] T008 [P] [US1] Unit tests for the conversation builder in `tests/worker/test_conversation.py` (model-deployment case): builds `DialConversation` with two messages — user content from snapshot `payload.messages`, assistant content from `result.choices[0].message.content` — `model.id` == deployment id, correct `id`/`folderId`, `name` derived from prompt.
- [x] T009 [P] [US1] Unit tests in `tests/worker/test_conversation.py` (orchestration): on success + flag true, resolves bucket then writes conversation via `DialClient`; skips entirely when flag false; deployment-kind detection routes a non-`applications/` deployment to the model path (no `get_application` call).
- [x] T010 [P] [US1] Unit tests in `tests/worker/test_jobs.py`: conversation creation invoked only after `_persist_success` and only when `create_conversation=true`; not invoked on error/missed runs; run status unaffected by whether creation runs.

### Implementation for User Story 1

- [x] T011 [US1] Add `create_conversation: bool = False` to `ScheduleProperties` and extend `validate_target_rules` in `src/lib/schemas/schedule.py` to reject it for non-chat-completion targets. Makes T007 pass.
- [x] T012 [US1] Implement conversation builder + orchestration in `src/worker/conversation.py`: `build_conversation(...)` (assemble `DialConversation` from run snapshot + assembled result) and `create_run_conversation(ctx, properties, result)` (detect deployment kind, resolve bucket, resolve model id for the model case, write via `DialClient`); structured `logger` events (`conversation_build`, `conversation_create_start/succeeded/failed`) with run_id/schedule_id/deployment kind, no content. Depends on T005, T006. Makes T008, T009 pass (model path).
- [x] T013 [US1] Wire best-effort invocation into `src/worker/jobs.py`: after `_persist_success` in `_execute_scheduled_job_inner`, if `properties.target_type == CHAT_COMPLETION and properties.create_conversation`, call `create_run_conversation(...)` inside a try/except that catches `ConversationCreationError`/`Exception`, logs `conversation_create_failed`, and returns without altering run success (FR-013). Depends on T012. Makes T010 pass.

**Checkpoint**: US1 fully functional for model deployments — MVP deliverable.

---

## Phase 4: User Story 2 - Correct model attribution for applications (Priority: P2)

**Goal**: For application deployments (`applications/...`), resolve the app's underlying `reference` via `GET /openai/applications/{app_id}` and use it as the conversation `model.id`; model deployments keep using the deployment id.

**Independent Test**: Run a schedule targeting an application deployment with `create_conversation=true`; confirm the created conversation's `model.id` equals the application's `reference`.

### Tests for User Story 2 (write first, ensure they FAIL) ⚠️

- [x] T014 [P] [US2] Unit tests in `tests/worker/test_conversation.py` (application case): a deployment id beginning `applications/` triggers `DialClient.get_application`; the returned `reference` becomes the conversation `model.id`; a resolution failure raises `ConversationCreationError` (non-fatal upstream) and matches `conversations/app/dial_conversation.json` model id `c9e5decf-...`.

### Implementation for User Story 2

- [x] T015 [US2] Extend deployment-kind resolution in `src/worker/conversation.py`: when the deployment id has the `applications/` prefix, call `get_application` and use `reference` as `model.id`; otherwise use the deployment id (FR-006/007/008). Depends on T012. Makes T014 pass.

**Checkpoint**: US1 (models) and US2 (applications) both produce correctly attributed conversations.

---

## Phase 5: User Story 3 - Preserve assistant custom content (Priority: P2)

**Goal**: The assistant message in the created conversation includes both textual `content` and any `custom_content` (tool_execution_history, stages) from the run result.

**Independent Test**: Run a schedule against a deployment that returns custom content; confirm the stored conversation's assistant message carries both content and custom_content.

### Tests for User Story 3 (write first, ensure they FAIL) ⚠️

- [x] T016 [P] [US3] Unit tests in `tests/worker/test_conversation.py`: assistant `DialMessage` includes `custom_content` when `result.choices[0].message.custom_content` is present (assert against `conversations/app` fixture); omits/None when absent (assert against `conversations/gpt-5-6` fixture); plain-text-only result yields content with no custom_content.

### Implementation for User Story 3

- [x] T017 [US3] Ensure `build_conversation` in `src/worker/conversation.py` copies `custom_content` (and optional `responseId`) from `result.choices[0].message` into the assistant `DialMessage` when present, otherwise leaves it unset (FR-005). Depends on T012. Makes T016 pass.

**Checkpoint**: Conversations faithfully reflect rich responses.

---

## Phase 6: User Story 4 - Unified streaming/non-streaming result (Priority: P3)

**Goal**: Chat completion runs store a single assembled `ChatCompletion` result for both streaming and non-streaming payloads, giving conversation creation one consistent input shape.

**Independent Test**: Run one streaming and one non-streaming chat_completion schedule; confirm both store an equivalent assembled completion and produce structurally equivalent conversations.

### Tests for User Story 4 (write first, ensure they FAIL) ⚠️

- [x] T018 [P] [US4] Update/add unit tests in `tests/worker/executors/test_chat_completion.py`: mock `client.chat.completions.stream` context manager; assert the executor iterates events (logging only `event_type`), calls `get_final_completion()`, and returns `{"result": <assembled ChatCompletion>.model_dump()}` for both `stream=true` and `stream=false` payloads; assert no streamed content is logged.

### Implementation for User Story 4

- [x] T019 [US4] Refactor `ChatCompletionExecutor.execute` in `src/worker/executors/chat_completion.py` to use `async with client.chat.completions.stream(**create_kwargs) as stream`, iterate events logging `event_type=getattr(stream_event, "type", None)` only, then `result = await stream.get_final_completion()` and return `{"result": result.model_dump()}`; remove the `create()`/`chunks` branch; preserve `extra_headers`, `timeout`, and `APIStatusError`/`Exception` → `ScheduleRunError` handling (FR-012, FR-015). Makes T018 pass.

**Checkpoint**: Result shape unified; US1–US3 conversations work identically for streaming and non-streaming.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates, coverage, and end-to-end validation across all stories.

- [x] T020 [P] Add/verify structured-logging assertions across `tests/worker/test_conversation.py` and `tests/worker/test_jobs.py` confirming no prompt/response content or credentials appear in log fields (FR-015).
- [x] T021 Run and fix `uv run ruff check src tests` and `uv run ruff format src tests`.
- [x] T022 Run and fix `uv run mypy src`.
- [x] T023 Run full suite with coverage: `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml`; ensure all new code covered.
- [~] T024 Execute `specs/027-create-run-conversation/quickstart.md` scenarios 1–6. Automated coverage (scenarios 1–6 as unit/integration tests) passes. Live end-to-end validation against DIAL Core — the GET-based lookups (`/v1/bucket`, `/openai/applications/{id}`) were verified during planning; the actual conversation **PUT** write is deferred as it requires explicit user approval to mutate DIAL storage.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS US1–US3.
- **User Stories (Phases 3–6)**: US1/US2/US3 depend on Foundational. US4 is independent of Foundational (executor-only) and may run any time after Setup, but US1–US3 conversations are only fully equivalent across transports once US4 lands.
- **Polish (Phase 7)**: Depends on all targeted stories.

### User Story Dependencies

- **US1 (P1)**: After Foundational. No dependency on other stories (MVP).
- **US2 (P2)**: Builds on US1's `src/worker/conversation.py` (extends resolution) — sequence after T012.
- **US3 (P2)**: Builds on US1's `build_conversation` (T012) — sequence after T012; independent of US2.
- **US4 (P3)**: Independent (executor refactor). Can be done in parallel with US1–US3.

### Within Each User Story

- Tests written and failing before implementation (TDD).
- Schemas/models before services; services before wiring; core before integration.

### Parallel Opportunities

- Setup: T002 [P] alongside T001.
- Foundational tests T003, T004 [P] together; implementation T005 [P] then T006.
- US1 tests T007–T010 [P] together before implementation.
- US2/US3 test tasks (T014, T016) [P]; US4 (T018) [P] — can be authored in parallel by different developers.
- US4 (Phase 6) can proceed in parallel with US1–US3 since it touches only `chat_completion.py`.

---

## Parallel Example: User Story 1

```bash
# Author all US1 tests together (they must fail first):
Task: "Schedule validation tests in tests/lib/schemas/test_schedule.py"        # T007
Task: "Builder tests (model case) in tests/worker/test_conversation.py"        # T008
Task: "Orchestration tests in tests/worker/test_conversation.py"               # T009
Task: "Job wiring tests in tests/worker/test_jobs.py"                          # T010

# Then implement in dependency order: T011 → T012 → T013
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: Setup (T001–T002)
2. Phase 2: Foundational (T003–T006) — CRITICAL, blocks US1–US3
3. Phase 3: User Story 1 (T007–T013)
4. **STOP and VALIDATE**: model-deployment conversation created on success; flag validation enforced
5. Deploy/demo (MVP)

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 → test → MVP (model deployments)
3. US2 → application attribution → test/demo
4. US3 → custom content fidelity → test/demo
5. US4 → unified result shape → test/demo
6. Polish → quality gates + quickstart

### Parallel Team Strategy

After Foundational: Dev A on US1 (then US2/US3 extensions of `conversation.py` sequentially), Dev B on US4 (`chat_completion.py`) concurrently.

---

## Notes

- [P] = different files, no incomplete dependencies.
- No Alembic migration: `create_conversation` persists via `ScheduleProperties` JSONB → `kwargs`/snapshot.
- Conversation creation is best-effort and post-persistence — never fails a successful run.
- Verify tests fail before implementing; commit after each task or logical group.

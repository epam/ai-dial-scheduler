---

description: "Task list for Base URL and Payload Model for Chat Completion"
---

# Tasks: Base URL and Payload Model for Chat Completion

**Input**: Design documents from `/specs/029-base-url-model-field/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included — the project constitution (Principle III, Test-First) makes TDD mandatory. Tests
MUST be written and confirmed failing before implementation.

**Organization**: Tasks are grouped by user story. Note: because this feature changes a few shared
files (`schedule.py`, `chat_completion.py`, `conversation.py`, `dial.py`), the user stories touch
overlapping modules. Dependencies are called out explicitly; do not treat all `[US*]` tasks as
independently parallelizable across stories where they share a file.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Exact file paths are included in each description

## Path Conventions

Web service with `src/api`, `src/worker`, shared `src/lib`, tests mirror `src/` under `tests/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm environment; no new scaffolding is required for this feature.

- [X] T001 Verify dev environment: run `uv sync`, then confirm baseline green with `uv run pytest`, `uv run ruff check src tests`, and `uv run mypy src` before making changes.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No blocking foundational work — no new models, DB migrations, or infrastructure. Target
properties ride the existing JSONB `kwargs`; the `ScheduleProperties` schema shape is unchanged.

**Checkpoint**: None required. Proceed directly to User Story 1.

---

## Phase 3: User Story 1 - Configure chat completion schedule with a base URL and model (Priority: P1) 🎯 MVP

**Goal**: Chat completion uses `properties.url` as a base URL and selects the deployment from
`payload.model`; `payload.model` becomes required at validation time. Result shape unchanged for
stream and non-stream.

**Independent Test**: Create a chat-completion schedule with a base URL and `payload.model`, trigger a
run, and confirm the run calls the model named in the payload with the standard `{"result": {...}}`
shape; a schedule missing `payload.model` is rejected at create/update.

### Tests for User Story 1 (write first, confirm FAILING) ⚠️

- [X] T002 [P] [US1] In tests/unit/lib/schemas/test_schedule_schemas.py, add/adjust tests asserting `chat_completion` requires a non-empty `payload.model` (valid config passes with `stream` defaulted to `False`; missing `model` and empty `model` raise `ValueError`; `method`-set and missing-`api_version` cases still raise) per contracts/schedule-properties.md.
- [X] T003 [P] [US1] In tests/unit/worker/executors/test_chat_completion.py, rewrite tests to assert the client is built with `base_url=properties.url` (NOT `azure_endpoint`/`azure_deployment`), the payload `model` flows into the create/stream call, non-stream `.create()` and stream `.stream()`+`get_final_completion()` produce the identical `{"result": <dump>}` shape, `effective_timeout` is applied, and `APIStatusError` maps to `ScheduleRunError` with `status_code`, per contracts/chat-completion-executor.md.

### Implementation for User Story 1

- [X] T004 [US1] In src/lib/schemas/schedule.py, extend `validate_target_rules` so the `payload.model` required-check applies to `CHAT_COMPLETION` as well as `RESPONSES` (generalize the condition; use a single message like `"payload.model is required for target_type '{self.target_type}'"`). Keep `create_conversation` chat-completion-only.
- [X] T005 [US1] In src/worker/executors/chat_completion.py, construct `AsyncAzureOpenAI(base_url=properties.url, api_key=..., api_version=properties.api_version, http_client=...)`; remove the `parse_deployment` call and the `azure_endpoint`/`azure_deployment` arguments and the now-unused import. Leave `create_kwargs`, streaming, timeout, result-dump, and error mapping behavior unchanged.
- [X] T006 [US1] Run T002–T003 tests; iterate on src until green (Red→Green).

**Checkpoint**: Chat completion runs against a base URL using `payload.model`; validation rejects
missing model. MVP is functional and independently testable.

---

## Phase 4: User Story 3 - Conversation creation uses the payload model (Priority: P3)

> Sequenced before US2 (docs) because it is a code change that shares files with US1's cleanup.

**Goal**: Conversation creation derives the deployment/model from `payload.model` instead of parsing
the URL, preserving application-reference resolution and non-fatal failure behavior.

**Independent Test**: Enable `create_conversation` on a chat-completion schedule with `payload.model`
set, run it, and confirm the saved conversation is attributed to `payload.model` (application prefix
resolved via `get_application`).

### Tests for User Story 3 (write first, confirm FAILING) ⚠️

- [X] T007 [P] [US3] In tests/unit/worker/test_conversation.py, update/add tests asserting `create_run_conversation` reads the deployment from `properties.payload["model"]` (plain model → `deployment_kind="model"`; application-prefixed model → `get_application` called and `model_id=details.reference`, `deployment_kind="application"`; missing `payload.model` → `ConversationCreationError`), per contracts/conversation-model.md.

### Implementation for User Story 3

- [X] T008 [US3] In src/worker/conversation.py, replace `extract_deployment_id(properties.url)` with reading `properties.payload.get("model")`; raise `ConversationCreationError` when missing. Remove the now-unused `extract_deployment_id` import. Keep `APPLICATION_DEPLOYMENT_PREFIX` branching, logging, bucket resolution, and `put_conversation` unchanged.
- [X] T009 [US3] Run T007 tests; iterate until green.

**Checkpoint**: Conversation attribution is driven by `payload.model`; US1 and US3 both functional.

---

## Phase 5: User Story 2 - Consistent behavior between chat completion and responses (Priority: P2)

**Goal**: Documentation and examples describe one convention (base URL + required `payload.model`) for
both chat completion and responses; responses behavior is unchanged.

**Independent Test**: Read documentation/properties.md and the e2e example and confirm chat completion
and responses share the base-URL + `payload.model` convention.

### Implementation for User Story 2

- [X] T010 [P] [US2] In documentation/properties.md, update the `url` and `payload` descriptions so chat completion is documented as a base URL (no `/deployments/{name}/chat/completions` path) with `payload.model` required, matching the responses wording.
- [X] T011 [P] [US2] Update the chat-completion e2e example under tests/e2e/*.http to use a base URL and a `payload.model` field (remove any deployment path segment from the URL).

**Checkpoint**: Documentation and examples are consistent across both LLM target types.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Remove now-dead code and run all quality gates. These depend on US1 (T005) and US3 (T008)
having removed the last callers of the URL-parsing helpers.

- [X] T012 In src/lib/utils/dial.py, remove `parse_deployment` and `extract_deployment_id` (keep `strip_bearer_prefix`). Confirm no remaining references via a repo search before deleting.
- [X] T013 In tests/unit/lib/utils/test_dial.py, remove the `parse_deployment`/`extract_deployment_id` tests; keep `strip_bearer_prefix` tests.
- [X] T014 Run full quality gates and fix issues: `uv run pytest --cov=src`, `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`.
- [X] T015 Execute the quickstart validation in specs/029-base-url-model-field/quickstart.md (validation + run checks) and confirm expected outcomes.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: None for this feature.
- **User Story 1 (Phase 3)**: Starts after Setup. Delivers the MVP.
- **User Story 3 (Phase 4)**: Independent of US1 at the code level (different file, `conversation.py`), but its tests assume the US1 config convention; sequence after US1 for a coherent build.
- **User Story 2 (Phase 5)**: Docs/examples only; do after US1 so wording matches shipped behavior.
- **Polish (Phase 6)**: T012–T013 depend on T005 (US1) and T008 (US3) removing the helper callers. T014–T015 run last.

### Critical dependency note

- Do NOT delete the `dial.py` helpers (T012) until both T005 (chat_completion executor) and T008
  (conversation) no longer import them — otherwise the module breaks.

### Within Each User Story

- Tests are written first and MUST fail before implementation (Red→Green).
- Validation-rule change (T004) and executor change (T005) can proceed together but both live under
  US1; run their shared test file updates (T002/T003) first.

### Parallel Opportunities

- T002 and T003 touch different test files → parallelizable.
- T010 and T011 (docs + e2e) → parallelizable.
- Across stories, US1 (`schedule.py`, `chat_completion.py`) and US3 (`conversation.py`) touch
  different source files and could be developed in parallel by different people, but Polish (T012)
  must wait for both.

---

## Parallel Example: User Story 1

```bash
# Write the failing tests together (different files):
Task: "Validation tests in tests/unit/lib/schemas/test_schedule_schemas.py"   # T002
Task: "Executor tests in tests/unit/worker/executors/test_chat_completion.py" # T003
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup → baseline green.
2. Phase 3 US1 (T002–T006) → chat completion uses base URL + `payload.model`, validation enforced.
3. **STOP and VALIDATE**: run US1 tests; the core feature is deliverable.

### Incremental Delivery

1. US1 (MVP) → US3 (conversation attribution) → US2 (docs/examples) → Polish (dead-code removal +
   gates + quickstart).
2. Each increment keeps the suite green.

---

## Notes

- [P] = different files, no dependencies.
- No DB migration (target properties live in JSONB `kwargs`); `ScheduleProperties` OpenAPI shape
  unchanged.
- Breaking change: legacy chat-completion schedules with a deployment path in the URL and no
  `payload.model` will fail validation after T004 — see spec Assumptions.
- Commit after each task or logical group; verify tests fail before implementing.

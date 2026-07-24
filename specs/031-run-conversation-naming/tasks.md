---
description: "Task list for Run Conversation Naming Convention"
---

# Tasks: Run Conversation Naming Convention

**Input**: Design documents from `specs/031-run-conversation-naming/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/conversation-name.md, quickstart.md

**Tests**: REQUIRED — the project constitution mandates TDD (Principle III: Test-First, NON-NEGOTIABLE). All test tasks below MUST be written and confirmed FAILING before their implementation tasks.

**Organization**: Tasks grouped by user story. The pure naming logic is shared by both stories and lives in Phase 2 (Foundational).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 or US2 (maps to spec.md user stories)
- Exact file paths included in every task

## Path Conventions

Single web-service layout (existing): `src/lib/`, `src/worker/`, `tests/unit/`. No migration, no API contract change (per plan.md).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the constants the naming logic depends on.

- [X] T001 Add naming constants to `src/lib/constants.py`: `CONVERSATION_NAME_SEPARATOR = "__"`, `CONVERSATION_NAME_SEGMENT_PLACEHOLDER = "untitled"`, `CONVERSATION_NAME_SEGMENT_MAX_LENGTH = 64` (values per data-model.md / research Decision 4)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The pure `slugify` + `build_conversation_name` helpers. Both user stories depend on these. No I/O.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests for Foundational (write FIRST, confirm FAILING) ⚠️

- [X] T002 [P] Create `tests/unit/lib/utils/test_naming.py` covering `slugify` per contracts/conversation-name.md: `slugify("Daily GPT-4o Summary!", 64) == "daily-gpt-4o-summary"`; `slugify("  Weird__Name  ", 64) == "weird-name"`; `slugify("!!!", 64) == "untitled"`; `slugify("Привет", 64) == "untitled"`; `slugify("a"*80, 64)` has length 64 and no trailing `-`; output never contains `"__"`; output matches `^[a-z0-9-]*$`; truncation that lands on a hyphen strips the trailing `-`
- [X] T003 [P] Add `build_conversation_name` tests to `tests/unit/lib/utils/test_naming.py`: canonical `build_conversation_name("chathub-claude4", "hello", "964ff385-72e7-46f8-8cdc-fa3358b825ec") == "chathub-claude4__hello__964ff385-72e7-46f8-8cdc-fa3358b825ec"`; `.split("__", 2)` recovers 3 segments with the run id verbatim as the last; empty display name → `chathub-claude4__untitled__{run_id}`; two different run ids produce different names (SC-003); determinism (same inputs → same output, FR-008)

### Implementation for Foundational

- [X] T004 Create `src/lib/utils/naming.py` implementing `slugify(value: str, max_length: int) -> str` (lowercase → collapse `[^a-z0-9]+` to single `-` → strip leading/trailing `-` → truncate to `max_length` then strip trailing `-` → return `CONVERSATION_NAME_SEGMENT_PLACEHOLDER` if empty) using stdlib `re` only, with a Google-style docstring; make T002 pass
- [X] T005 In `src/lib/utils/naming.py` add `build_conversation_name(deployment_id: str, display_name: str, run_id: str) -> str` returning `slugify(deployment_id, MAX) + SEP + slugify(display_name, MAX) + SEP + run_id` (SEP/MAX from constants); make T003 pass

**Checkpoint**: Pure naming logic complete and unit-tested in isolation.

---

## Phase 3: User Story 1 - Recognize a run's conversation by its name (Priority: P1) 🎯 MVP

**Goal**: Auto-created run conversations are named `{deployment-slug}__{display-name-slug}__{run-id}` instead of the bare run id.

**Independent Test**: Enable conversation creation on a chat completion schedule with deployment id "chathub-claude4" and display name "hello"; a completed run yields a conversation named `chathub-claude4__hello__{run_id}`.

### Tests for User Story 1 (write FIRST, confirm FAILING) ⚠️

- [X] T006 [P] [US1] Update `tests/unit/worker/test_conversation.py`: add `deployment_id` and `display_name` to the `_BUILD_INPUT` fixture and any `ConversationBuildInput(...)` constructions; assert `build_conversation(...).name == build_conversation_name(deployment_id, display_name, run_id)` (the three-part name), replacing the current bare-`run_id` expectation
- [X] T007 [P] [US1] Add assertions to `tests/unit/worker/test_conversation.py` that `build_conversation` does NOT change conversation `id`, `folderId`, or `messages` when the name changes (FR-007 regression guard)
- [X] T008 [P] [US1] Add a `create_run_conversation` test in `tests/unit/worker/test_conversation.py` verifying the schedule display name is fetched (mock `ScheduleRepository.get_by_id` returning a schedule with `display_name="hello"`) and `deployment_id` is taken from `properties.payload["model"]`, producing the expected conversation name

### Implementation for User Story 1

- [X] T009 [US1] Add `deployment_id: str` and `display_name: str` fields to `ConversationBuildInput` in `src/lib/schemas/conversation.py` (per contracts/conversation-name.md), keeping the model docstring accurate
- [X] T010 [US1] Update `build_conversation` in `src/worker/conversation.py` to set `name=build_conversation_name(build_input.deployment_id, build_input.display_name, build_input.run_id)` (import from `src.lib.utils.naming`); leave `id`, `folderId`, `messages`, and all other fields unchanged; make T006/T007 pass
- [X] T011 [US1] Update `create_run_conversation` in `src/worker/conversation.py` to fetch the schedule via `ScheduleRepository.get_by_id(ctx.schedule_id)` (open a DB session as done elsewhere in the worker), and populate `ConversationBuildInput` with `deployment_id=deployment` (the raw `payload["model"]`, before application-reference resolution) and `display_name` from the fetched schedule; make T008 pass
- [X] T012 [US1] Verify/adjust the `_maybe_create_conversation` → `create_run_conversation` call path in `src/worker/jobs.py` so the display-name fetch failure stays best-effort (already swallowed at `jobs.py:83`); add no new required logging fields that expose content

**Checkpoint**: US1 fully functional — MVP. New conversations carry the three-part name; storage path unchanged.

---

## Phase 4: User Story 2 - Stable, unambiguous naming across edge inputs (Priority: P2)

**Goal**: Empty-after-slugification, non-Latin, and overly long display names never fail conversation creation and always yield a valid, readable, unique name.

**Independent Test**: Schedules with a punctuation-only display name, a very long display name, and a non-Latin display name each produce a valid name whose run id segment uniquely identifies the run; conversation creation succeeds in all cases.

> The core logic for these cases already lives in the Phase 2 `slugify` helper. This phase adds end-to-end assertions at the conversation-build/orchestration level to prove robustness (SC-004), rather than new production code.

### Tests for User Story 2 (write FIRST, confirm FAILING) ⚠️

- [X] T013 [P] [US2] Add cases to `tests/unit/worker/test_conversation.py` (parametrized): schedule `display_name` of only punctuation → conversation name `{deployment-slug}__untitled__{run_id}`; a very long `display_name` → display segment truncated to 64 chars, run id segment intact; a non-Latin `display_name` → `untitled` segment; assert `create_run_conversation` completes without raising for each (SC-004)
- [X] T014 [P] [US2] Add a case asserting two runs of the same schedule (different `run_id`) produce different conversation names (SC-003) at the `build_conversation` level

### Implementation for User Story 2

- [X] T015 [US2] Confirm no additional production changes are required beyond Phase 2/US1 (the placeholder/truncation guarantees come from `slugify`); if any edge case in T013/T014 fails, fix it in `src/lib/utils/naming.py` only, keeping `slugify`/`build_conversation_name` pure and deterministic

**Checkpoint**: US1 and US2 both pass; naming is robust against all documented edge inputs.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates and validation across the feature.

- [X] T016 Run `uv run pytest` and fix any failing tests
- [X] T017 [P] Run `uv run ruff check src tests` and `uv run ruff format --check src tests`; fix issues (auto-fix where ruff supports it)
- [X] T018 [P] Run `uv run mypy src` and resolve any type errors introduced by the new field/module
- [X] T019 Execute the quickstart validation in `specs/031-run-conversation-naming/quickstart.md` (unit + integration sections) and confirm all Success Criteria (SC-001..SC-004) hold

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: T001 — no dependencies; start immediately.
- **Foundational (Phase 2)**: depends on T001 (constants). BLOCKS all user stories.
- **User Story 1 (Phase 3)**: depends on Phase 2. This is the MVP.
- **User Story 2 (Phase 4)**: depends on Phase 2; naturally validated after US1 wires the builder. Independently testable.
- **Polish (Phase 5)**: depends on all desired stories complete.

### User Story Dependencies

- **US1 (P1)**: needs the Phase 2 helper + constants. No dependency on US2.
- **US2 (P2)**: needs the Phase 2 helper; shares the `build_conversation`/`create_run_conversation` code touched by US1, so run after US1 for a clean single-file edit history (edits to `test_conversation.py` and `naming.py` overlap).

### Within Each Story

- Tests written and FAILING before implementation (TDD, Principle III).
- Constants (T001) → helper (T004, T005) → schema field (T009) → builder (T010) → orchestration (T011, T012).

### Parallel Opportunities

- T002 and T003 [P] — same new test file but independent test blocks; may be authored together.
- T006, T007, T008 [P] — independent assertions/cases (author before US1 implementation).
- T013, T014 [P] — independent parametrized cases.
- T017, T018 [P] — ruff and mypy are independent commands.
- Note: T004/T005 both edit `src/lib/utils/naming.py` → NOT parallel with each other. T009/T010/T011 form a dependency chain → sequential.

---

## Parallel Example: Foundational tests

```bash
# Author the failing helper tests together (same file, independent blocks):
Task: "slugify edge-case tests in tests/unit/lib/utils/test_naming.py"          # T002
Task: "build_conversation_name tests in tests/unit/lib/utils/test_naming.py"    # T003
```

## Parallel Example: User Story 1 tests

```bash
Task: "build_conversation name assertion in tests/unit/worker/test_conversation.py"        # T006
Task: "FR-007 id/folderId/messages regression guard in tests/unit/worker/test_conversation.py"  # T007
Task: "create_run_conversation display-name fetch test in tests/unit/worker/test_conversation.py" # T008
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 (T001) → Phase 2 (T002–T005) → Phase 3 (T006–T012).
2. **STOP and VALIDATE**: new conversations named `{deployment}__{display}__{run_id}`; storage path unchanged.
3. This is a shippable MVP on its own.

### Incremental Delivery

1. Setup + Foundational → pure naming logic proven.
2. US1 → conversations get recognizable names (MVP).
3. US2 → prove robustness on edge inputs (mostly test-driven; production code likely already correct).
4. Polish → run all quality gates + quickstart.

---

## Notes

- [P] = different files or independent blocks, no dependencies.
- No DB migration and no REST/OpenAPI change (plan.md); the only cross-service contract is the updated `ConversationBuildInput` Pydantic model (Principle IV).
- Only `DialConversation.name` changes; conversation `id`/path/messages untouched (FR-007).
- Verify each test FAILS before implementing (Principle III).
- Do not log prompt/response content or credentials (Principle II / Security).
- Commit after each phase or logical group.

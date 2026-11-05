---

description: "Task list for Conversation Path Encoding for Schedule Executions"
---

# Tasks: Conversation Path Encoding for Schedule Executions

**Input**: Design documents from `/specs/034-conversation-path-encoding/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/conversation-path.md](contracts/conversation-path.md), [quickstart.md](quickstart.md)

**Tests**: Included and mandatory. Constitution Principle III (Test-First) is NON-NEGOTIABLE — every test task must be written and confirmed **failing** before the implementation task that satisfies it.

**Organization**: Tasks are grouped by user story. Read the note below before planning parallel work.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

Backend service, `api/` + `worker/` + `lib/` layout per plan.md. Repository root holds `src/` and `tests/`. Tests mirror `src/` under `tests/unit/`.

---

## ⚠️ Read first: how independent these stories really are

All three user stories are delivered by **the same two functions in one file** (`_conversation_name`
and `create_run_conversation` in `src/worker/conversation.py`). They are *not* independently
deployable slices in the usual sense, and pretending otherwise would produce a misleading plan.

What that means in practice:

- **Independent test**: yes — each story has its own assertions that pass or fail on their own.
- **Independent implementation**: partially. US2's encoding work is a refinement of the same helper
  US1 uses, so US2 is verification-dominant (mostly tests plus one refinement).
- **Parallel across stories**: **no**. T012, T021, and T025 all edit `_conversation_name`. Assigning
  US1/US2/US3 to three people in parallel will produce merge conflicts in a ~15-line function.

Recommended execution: **sequential, single developer**, in phase order. Parallelism is available
*within* phases (marked `[P]`), not across stories. This is a small feature; the phase structure
exists for traceability and checkpointing, not for staffing fan-out.

---

## Phase 1: Setup

**Purpose**: Establish a known-good baseline before changing anything.

- [X] T001 Run the four quality gates on a clean tree and confirm all pass: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest`. Record the current pass count so regressions are attributable.
- [X] T002 [P] Confirm `src/worker/conversation.py:4` is the only `slugify` import across `src/` and `tests/` (grep both trees), documenting the result for the dependency removal in T031.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The encoding primitive and the transfer-object field that every user story depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Write failing unit tests for the not-yet-existing private encoding helper in `tests/unit/worker/test_conversation.py`, covering: round-trip `unquote(encode(v)) == v` for ASCII, non-ASCII (`Привет`), empty string, and strings containing `/`, `%`, spaces, and `_`; and asserting `encode(v)` never contains the literal `__` for any input including `a__b`. Confirm they FAIL.
- [X] T004 Implement `_encode_name_part(value: str) -> str` in `src/worker/conversation.py` as `quote(value, safe="").replace("_", "%5F")`, importing `quote` from `urllib.parse`. The Google-style docstring MUST explain *why* the underscore replacement exists (underscore is RFC 3986 unreserved, so without this step a deployment id such as `Untitled app 1101__0.0.1` injects a false `__` separator — see research R-002). Without that rationale the line reads as noise and will be deleted by a future cleanup. Confirm T003 now passes.
- [X] T005 [P] Add the required `deployment_id: str` field to `ConversationBuildInput` in `src/lib/schemas/conversation.py`, alongside the existing `model_id`. Update the class docstring to state the split explicitly: `deployment_id` is path-facing and is the id as configured on the schedule; `model_id` is document-facing and is the resolved reference. Per [data-model.md](data-model.md).
- [X] T006 Update the `make_build_input` test helper in `tests/unit/worker/test_conversation.py` to supply `deployment_id`, defaulting it to the same value as `model_id` so existing model-backed cases are unaffected.

**Checkpoint**: The encoder exists and is proven correct in isolation; the build input can carry both identifiers. User story work can begin.

---

## Phase 3: User Story 1 - Conversations addressed by the deployment the user actually scheduled (Priority: P1) 🎯 MVP

**Goal**: The conversation path identifies the deployment configured on the schedule — the application itself, not its underlying model reference — using one rule for both deployment kinds.

**Independent Test**: Schedule against an application, execute, and confirm the stored path's deployment part decodes to the `applications/...` id while the resolved reference appears nowhere in the path.

### Tests for User Story 1 ⚠️

> Write these FIRST and confirm they FAIL before T012/T013.

- [X] T007 [P] [US1] Write failing test in `tests/unit/worker/test_conversation.py`: for an application-backed build input, `unquote(conv.name.split("__")[0])` equals the `applications/...` deployment id, and the resolved reference string does not appear anywhere in `conv.name` or `conv.id`. Covers FR-001, FR-003, SC-001.
- [X] T008 [P] [US1] Write failing test in `tests/unit/worker/test_conversation.py`: `conv.model.id` still equals the resolved application `reference`, and each message's `model.id` matches it. This is the FR-010 / SC-007 guard against the reference being purged from the document as well as the path. Covers spec US1 + FR-010.
- [X] T009 [P] [US1] Write failing test in `tests/unit/worker/test_conversation.py`: two build inputs with different application deployment ids but the identical resolved `model_id` produce different `conv.name` and `conv.id`. Covers spec US1 acceptance scenario 2.
- [X] T010 [P] [US1] Write failing test in `tests/unit/worker/test_conversation.py`: a model-backed build input (where `deployment_id == model_id`) produces a deployment part that decodes to the model id, exercising the same code path with no type branch. Covers FR-002, US1 acceptance scenario 3.
- [X] T011 [P] [US1] Write failing test in `tests/unit/worker/test_conversation.py` for `create_run_conversation`: with an application payload, `get_application` is still awaited exactly once with the raw deployment id, and the conversation written to `put_conversation` has the application id in its name and the reference in `model.id`. Covers FR-010 and pins the lookup against removal.

### Implementation for User Story 1

- [X] T012 [US1] Update `_conversation_name` in `src/worker/conversation.py` to build its first part from `_encode_name_part(build_input.deployment_id)` instead of `build_input.model_id`. Leave `conversation_resource_path` and `build_conversation` structurally unchanged — they consume the name and derive `id`/`folderId` as before (research R-005). **Not parallel with T021/T025: same function.**
- [X] T013 [US1] In `create_run_conversation` in `src/worker/conversation.py`, populate `ConversationBuildInput.deployment_id` from the raw `properties.payload["model"]` while leaving `model_id` resolution exactly as it is (application `reference`, else the deployment id). **Retain the `get_application` call** — FR-010 depends on it and it will look like dead code to anyone reading only FR-003 (research R-004).
- [X] T014 [US1] Update the existing test `TestCreateRunConversationApplication::test_application_deployment_resolves_reference` in `tests/unit/worker/test_conversation.py`: the reference must still reach `model.id`, but the assertion that it appears in the name is now inverted. Update, do not delete.

**Checkpoint**: Application-backed conversations are filed under the application. T007–T011 green.

---

## Phase 4: User Story 2 - Deployment ids with reserved characters stored safely (Priority: P1)

**Goal**: Deployment ids containing separators, reserved characters, or non-ASCII text produce exactly one well-formed path segment that decodes losslessly.

**Independent Test**: Build conversations from deployment ids containing `/`, `__`, spaces, and non-ASCII characters; confirm each final segment contains no bare `/`, splits into exactly three parts, and decodes to the original.

**Note**: The encoder from T004 already delivers most of this. This phase is verification-dominant — it exists to prove the P1 guarantee holds against the adversarial inputs that actually occur in this repo, not to add new machinery.

### Tests for User Story 2 ⚠️

- [X] T015 [P] [US2] Write failing test in `tests/unit/worker/test_conversation.py`: a deployment id containing `/` (e.g. `applications/bkt/app`) yields a final path segment containing no bare `/`, so `conv.id.rsplit("/", 1)[0]` still equals `conv.folderId`. Covers FR-004, US2 acceptance scenario 1.
- [X] T016 [P] [US2] Write failing test in `tests/unit/worker/test_conversation.py` using the real fixture id `applications/bkt/Untitled app 1101__0.0.1`: `conv.name.split("__")` returns exactly 3 parts, and part 0 decodes to that exact id. This is the regression guard for research R-002. Covers FR-008.
- [X] T017 [P] [US2] Write failing test in `tests/unit/worker/test_conversation.py`: a deployment id containing non-ASCII characters round-trips exactly through `unquote`. Covers FR-007, SC-005.
- [X] T018 [P] [US2] Write failing test in `tests/unit/lib/clients/test_dial_client.py`: `put_conversation` called with a path containing `%2F` and `%20` issues a request whose `httpx.URL(...).raw_path` still contains those escapes — asserting on `raw_path`, **never** on `.path`, which shows a decoded view and would falsely suggest double-decoding (research R-001).

### Implementation for User Story 2

- [X] T019 [US2] Confirm T015–T018 pass with the T004 encoder unchanged. If T016 fails, the underscore replacement is missing or was dropped — restore it in `src/worker/conversation.py` rather than weakening the test.
- [X] T020 [US2] Confirm `src/lib/clients/dial_client.py` requires no change and leave it untouched, recording the T018 result as the evidence.

**Checkpoint**: Adversarial deployment ids are safe. T015–T018 green with no new production code beyond T004.

---

## Phase 5: User Story 3 - Schedule names preserved exactly, not flattened (Priority: P2)

**Goal**: The schedule name reaches the path URL-encoded and verbatim — never lowercased, transliterated, hyphenated, or truncated.

**Independent Test**: Create schedules named with mixed case, spaces, punctuation, non-Latin script, and 80+ characters; confirm each name part decodes to the original exactly.

### Tests for User Story 3 ⚠️

- [X] T021 [P] [US3] Write failing test in `tests/unit/worker/test_conversation.py`: a schedule named `Daily Report Sync` yields a name part decoding to exactly that — capitalisation and spaces intact, no hyphens. Covers FR-005, FR-006, SC-003.
- [X] T022 [P] [US3] Write failing test in `tests/unit/worker/test_conversation.py`: schedules named `Weekly Sync` and `weekly-sync` produce different `conv.name` values. Covers SC-004.
- [X] T023 [P] [US3] Write failing test in `tests/unit/worker/test_conversation.py`: a schedule name containing `__` and `/` round-trips exactly and still yields exactly 3 parts from `split("__")`. Covers FR-008, US3 acceptance scenario 4.
- [X] T024 [P] [US3] Write failing test in `tests/unit/worker/test_conversation.py`: an 80-character name is **not** truncated — the decoded name part has all 80 characters. Covers research R-003.

### Implementation for User Story 3

- [X] T025 [US3] Update `_conversation_name` in `src/worker/conversation.py` to build its second part from `_encode_name_part(build_input.display_name)`, removing the `slugify(...)` call, the `max_length` argument, and the `from slugify import slugify` import. Update the function docstring, which currently describes slugification. **Not parallel with T012: same function.**
- [X] T026 [US3] Update the existing parametrized test `TestConversationNameEdgeInputs::test_build_conversation_handles_edge_display_names` in `tests/unit/worker/test_conversation.py`: the `("Привет", "privet")` case now expects a percent-encoded round-trip rather than transliteration, and the `("a"*80, "a"*64)` case now expects no truncation. Update, do not delete — these encode the old contract and their inversion is the proof of change.
- [X] T027 [US3] Update the existing tests `test_name_follows_convention` and `test_id_and_folder_follow_convention` in `tests/unit/worker/test_conversation.py` to the new expected name, keeping `test_id_final_segment_matches_name` (FR-009) and the `folderId` assertion (research R-005) passing unchanged in intent.
- [X] T028 [US3] Remove `CONVERSATION_NAME_SEGMENT_MAX_LENGTH` from `src/lib/constants.py` and its import in `src/worker/conversation.py`, after confirming no remaining references anywhere in `src/` or `tests/`.

**Checkpoint**: All three stories green. Slugification is gone from the codebase.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T029 Remove the `python-slugify>=8.0.4` dependency from `pyproject.toml` (line 29), run `uv sync`, and re-run the full suite to confirm nothing resolved it transitively. Depends on T002 and T028.
- [X] T030 [P] Extend `tests/e2e/1.3-Create-chat-completion-schedule-oauth-with-conversation-creation.http` to cover an application deployment whose id contains a space or `__`, alongside the existing model case, per [quickstart.md](quickstart.md) step 4.
- [X] T031 [P] Add a test in `tests/unit/worker/test_conversation.py` asserting `conv.name` is byte-identical to the final segment of `conv.id` for an adversarial input (application id with `__`, name with spaces), locking FR-009.
- [X] T032 Add or confirm a test that a `ConversationCreationError` raised inside `create_run_conversation` is caught by `_maybe_create_conversation` in `src/worker/jobs.py`, logged once as `conversation_create_failed` with `run_id` and `schedule_id`, and does not alter the run outcome. `src/worker/jobs.py:81-89` must remain untouched (FR-011).
- [X] T033 [P] Verify no schedule name, deployment id, prompt content, or credential value is newly introduced into any log statement in `src/worker/conversation.py` (Constitution II and the Security constraints).
- [X] T034 Run all four quality gates: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml`. Fix any findings; ruff auto-fixes first, then manual.
- [X] T035 Spawn `code-review-agent` with no main-agent context, read its report from this spec directory, address every comment, and re-run it until clean — per the constitution's Project SDLC.
- [ ] T036 Walk [quickstart.md](quickstart.md) end to end, including the live-DIAL checks in step 4, and confirm an application-backed conversation still opens in DIAL Chat exactly as before (SC-007).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: no dependencies.
- **Phase 2 (Foundational)**: after Setup. **Blocks all user stories** — the encoder and the `deployment_id` field are prerequisites for every story.
- **Phase 3 (US1, P1)**: after Phase 2.
- **Phase 4 (US2, P1)**: after Phase 2. Shares the encoder with US1; its tests are meaningful only once T012 routes `deployment_id` into the name, so run it after Phase 3.
- **Phase 5 (US3, P2)**: after Phase 2. T025 edits the same function as T012 — run after Phase 3.
- **Phase 6 (Polish)**: after Phases 3–5. T029 additionally depends on T028.

### Critical path

```text
T001 → T003 → T004 → T005 → T006 → T012 → T013 → T025 → T028 → T029 → T034 → T035 → T036
```

### Same-file serialization (do not parallelize)

- `src/worker/conversation.py::_conversation_name` — T012 and T025.
- `src/worker/conversation.py::create_run_conversation` — T013.
- `tests/unit/worker/test_conversation.py` — most test tasks touch this one file. The `[P]` markers on
  them mean *logically independent*, safe to author in one sitting; they are not an invitation to run
  four concurrent agents against the same file.

### Parallel Opportunities

- T002 alongside T001.
- T005 alongside T003/T004 (different file).
- Within Phase 3: T007–T011 are independent assertions, authorable together.
- Within Phase 4: T015–T017 together; T018 is genuinely parallel (different file).
- Within Phase 5: T021–T024 together.
- Within Phase 6: T030, T031, T033 are genuinely parallel (different files/concerns).

---

## Parallel Example: User Story 1

```bash
# Author these five assertions in one pass, then run them together and confirm all FAIL:
Task: "T007 app deployment id in name, reference absent from path"
Task: "T008 model.id still equals resolved reference (FR-010 guard)"
Task: "T009 two apps sharing a backing model produce distinct paths"
Task: "T010 model-backed schedule uses the identical rule"
Task: "T011 get_application still awaited; written doc carries both identifiers"

uv run pytest tests/unit/worker/test_conversation.py -v   # expect failures, then implement T012/T013
```

---

## Implementation Strategy

### MVP scope

**Phases 1 + 2 + 3 (T001–T014)** — the application-vs-reference defect is the reason this feature
exists, and fixing it is independently valuable and shippable. US3 (name encoding) is a P2 quality
improvement that can follow.

Be aware of one wrinkle if you ship the MVP alone: after Phase 3 the deployment part is encoded while
the name part is still slugified. That is coherent and correct, but it means two naming conventions
appear in production if US3 lands in a later release. Prefer landing Phases 3–5 together unless there
is a reason to split.

### Incremental delivery

1. Phases 1–2 → encoder proven, nothing user-visible changed yet.
2. Phase 3 → application conversations correctly attributed (**MVP**).
3. Phase 4 → adversarial ids proven safe (no new production code).
4. Phase 5 → names preserved exactly; slugify gone.
5. Phase 6 → dependency dropped, gates green, review clean.

### Team strategy

Single developer, sequential. See the note at the top of this file — three stories over ~15 lines of
shared code do not decompose across people, and attempting it costs more in conflicts than it saves.

---

## Notes

- Verify every test FAILS before writing the implementation it covers (Constitution III).
- Never assert on `httpx.URL.path` — it is decoded. Use `raw_path` (research R-001).
- Two changes will look like dead code to a reader who has not read the research: the
  `.replace("_", "%5F")` in T004 and the retained `get_application` call in T013. Both are load-bearing.
  Their docstrings must say so.
- Commit after each phase checkpoint.
- Out of scope, do not add: a `max_length` validator on `ScheduleRequest.display_name`, migration of
  existing conversations, and any change to `DialClient` or the REST API.

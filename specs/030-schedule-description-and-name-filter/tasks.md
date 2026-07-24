---

description: "Task list for Schedule Description & Name Filter"
---

# Tasks: Schedule Description & Name Filter

**Input**: Design documents from `specs/030-schedule-description-and-name-filter/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/schedules-api.md, quickstart.md

**Tests**: INCLUDED — the project constitution (Principle III, Test-First NON-NEGOTIABLE) mandates TDD. Every test task MUST be written and confirmed failing before its implementation task.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1, US2, US3, US4 (maps to spec.md user stories)
- Exact file paths are included in each task.

## Path Conventions

Single web-service repo per plan.md: source under `src/api/` and `src/lib/`, tests mirror under `tests/`, migrations under `migrations/versions/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the working environment before changes.

- [ ] T001 Verify dependencies and DB are ready: run `uv sync`, confirm `uv run alembic current` shows head at `c3f1a2b4d5e6` (add_service_id_to_schedules). ⚠️ BLOCKED: local PostgreSQL is offline (`ConnectionRefusedError [WinError 1225]`); `c3f1a2b4d5e6` confirmed as latest head via migration files. Run when DB is available.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The `description` column and model field are shared by US1 and US2 and MUST exist before either story's code. The name-filter dependency scaffold is shared groundwork for US3.

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete.

- [X] T002 Create Alembic migration `add_description_to_schedules` in `migrations/versions/`: `upgrade()` adds `sa.Column("description", sa.String(length=1000), nullable=True)` to `schedules`; `down_revision = "c3f1a2b4d5e6"`; `downgrade()` drops the column. Do NOT add an index (description is neither filtered nor sorted).
- [X] T003 Add `description: str | None = Field(default=None, sa_type=String(1000), nullable=True)` to the `Schedule` model in `src/lib/models/schedule.py` (place after `service_id`; keep the existing `metadata` column and GIN index unchanged).
- [ ] T004 Apply the migration: `uv run alembic upgrade head`; confirm the `description` column exists on `schedules` and pre-existing rows show NULL. ⚠️ BLOCKED: PostgreSQL offline in this environment. Run when DB is available.

**Checkpoint**: Schema and ORM model carry `description`; user stories can now begin.

---

## Phase 3: User Story 1 - Describe a schedule (Priority: P1) 🎯 MVP

**Goal**: Accept an optional, length-capped `description` on create/update and persist it (replace-on-update).

**Independent Test**: Create a schedule with a description → it persists; update replaces it; omit → NULL; over 1000 chars → 422.

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T005 [P] [US1] Unit tests for description validation in `tests/lib/schemas/test_schedule.py`: `ScheduleRequest` accepts a description, defaults to `None` when omitted, accepts exactly 1000 chars, rejects 1001 chars (ValidationError), and accepts a whitespace-only description.
- [X] T006 [P] [US1] Unit tests for repository persistence in `tests/lib/repositories/test_schedule_repository.py`: `create` stores `description`; `update` replaces it (including setting it back to `None`).
- [ ] T007 [P] [US1] ⚠️ BLOCKED (DB offline) — Integration tests in `tests/api/v1/endpoints/test_schedules.py`: `POST /schedules/` with a description returns 201 echoing it; `POST` with a 1001-char description returns 422 on `body.description` and creates nothing; `PUT` replaces the description.

### Implementation for User Story 1

- [X] T008 [US1] Add `description: str | None = Field(default=None, max_length=1000)` to `ScheduleRequest` in `src/lib/schemas/schedule.py` (no strip/non-empty validator — whitespace is allowed).
- [X] T009 [US1] Thread description through the repository in `src/lib/repositories/schedule_repository.py`: `update(...)` accepts `description: str | None` and assigns `schedule.description = description` unconditionally (full replacement).
- [X] T010 [US1] Thread description through `ScheduleService` in `src/lib/services/schedule_service.py`: pass `request.description` into the `Schedule(...)` constructor in `create`, capture `prior_schedule.description` and pass `description=request.description` into `repo.update` in `update`, and restore `description=prior_description` in the compensating rollback path.

**Checkpoint**: Descriptions are persisted and length-validated; T005–T007 pass.

---

## Phase 4: User Story 2 - See the description on details and list (Priority: P1)

**Goal**: Return the stored `description` on the details response and on every list row.

**Independent Test**: `GET /schedules/{id}` and `GET /schedules/` both include `description` (value or `null`), matching what was stored.

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T011 [P] [US2] Unit tests in `tests/lib/services/test_schedule_service.py`: `_to_response` includes `description`; the `list` mapping populates `description` on each `ScheduleListItem` (value and `None` cases).
- [ ] T012 [P] [US2] ⚠️ BLOCKED (DB offline) — Integration assertions in `tests/api/v1/endpoints/test_schedules.py`: details response and every list row include `description`; a schedule created without one returns `null` in both.

### Implementation for User Story 2

- [X] T013 [US2] Add `description: str | None = None` to `ScheduleResponse` and `ScheduleListItem` in `src/lib/schemas/schedule.py`.
- [X] T014 [US2] Populate description in `src/lib/services/schedule_service.py`: set `description=schedule.description` in `_to_response` and in the `ScheduleListItem(...)` construction inside `list`.

**Checkpoint**: Description visible on details and list; T011–T012 pass. (US1 + US2 = full description feature.)

---

## Phase 5: User Story 3 - Filter the schedules list by name (Priority: P2)

**Goal**: Optional `?name=` query param performs case-insensitive substring matching on `display_name`, composing with owner scope, metadata filters, and pagination.

**Independent Test**: `?name=report` returns only matching schedules (case-insensitive); empty/absent → full list; no match → empty set; composes with `metadata__*` and pagination.

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [X] T015 [P] [US3] Unit tests for the name-filter dependency in `tests/api/dependencies/test_name_filter.py`: returns the term when present, `None` when absent, and `None` for empty/whitespace-only values.
- [X] T016 [P] [US3] Unit tests for repository name filtering in `tests/lib/repositories/test_schedule_repository.py`: substring match, case-insensitivity, LIKE wildcards (`%`,`_`) treated literally, correct total count, owner scope preserved, and AND-composition with a metadata filter.
- [ ] T017 [P] [US3] ⚠️ BLOCKED (DB offline) — Integration tests in `tests/api/v1/endpoints/test_schedules.py`: `GET /schedules/?name=` scenarios D and E from quickstart.md (case-insensitive substring, empty → full list, no match → empty, composition with metadata + pagination).

### Implementation for User Story 3

- [X] T018 [P] [US3] Create `parse_name_filter` in `src/api/dependencies/name_filter.py`: read the optional `name` query param and return `str | None`, normalizing empty/whitespace-only to `None` (mirror the style of `src/api/dependencies/metadata_filter.py`).
- [X] T019 [US3] Add the name condition in `ScheduleRepository.list` in `src/lib/repositories/schedule_repository.py`: accept `name_filter: str | None = None`; when set, append `display_name_col.ilike("%" + escaped + "%")` (escape `%`, `_`, and the escape char) to the existing `conditions` list so it composes with owner scope and metadata containment for both the count and page statements.
- [X] T020 [US3] Thread `name_filter` through `ScheduleService.list` in `src/lib/services/schedule_service.py` (add the parameter and forward it to `repo.list`).
- [X] T021 [US3] Wire the dependency into the list endpoint in `src/api/v1/endpoints/schedules.py`: add a `NameFilterDep` annotated dependency and pass it into `service.list(...)`; update the endpoint docstring to document the `name` parameter.

**Checkpoint**: Name filtering works and composes; T015–T017 pass.

---

## Phase 6: User Story 4 - See the trigger on the list (Priority: P2)

**Goal**: Expose each schedule's full `trigger` definition on list rows, identical to details, alongside the retained `trigger_type`.

**Independent Test**: Each list row's `trigger` equals that schedule's details `trigger`; `trigger_type` is still present.

### Tests for User Story 4 (write first, confirm failing) ⚠️

- [X] T022 [P] [US4] Unit test in `tests/lib/services/test_schedule_service.py`: the `list` mapping sets `ScheduleListItem.trigger` to `schedule.trigger` and retains `trigger_type`.
- [ ] T023 [P] [US4] ⚠️ BLOCKED (DB offline) — Integration test in `tests/api/v1/endpoints/test_schedules.py`: for cron and date schedules, each list row includes both `trigger_type` and a `trigger` object identical to the details `trigger` (quickstart Scenario F).

### Implementation for User Story 4

- [X] T024 [US4] Add `trigger: dict[str, Any]` to `ScheduleListItem` in `src/lib/schemas/schedule.py` (keep `trigger_type`).
- [X] T025 [US4] Populate `trigger=schedule.trigger` in the `ScheduleListItem(...)` construction in `ScheduleService.list` in `src/lib/services/schedule_service.py` (reuse the already-loaded model value; no extra query).

**Checkpoint**: List rows carry the full trigger; T022–T023 pass.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Quality gates and end-to-end validation across all stories.

- [X] T026 Update the create/update OpenAPI examples in `src/api/v1/endpoints/schedules.py` to include a `description` field (optional but improves the contract docs).
- [X] T027 Run quality gates and fix issues: `uv run ruff check src tests`, `uv run ruff format src tests`, `uv run mypy src`.
- [X] T028 Run the full suite `uv run pytest --cov=src` and confirm all new tests pass with the changed lines covered. ✅ Unit suite: 627 passed. ⚠️ Integration tests (`tests/integration/`) not run — DB offline.
- [ ] T029 Execute `quickstart.md` Scenarios A–G manually (or via integration tests) to confirm end-to-end behavior and list/detail trigger parity. ⚠️ BLOCKED (DB offline) — requires a running API + PostgreSQL.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup. BLOCKS US1 and US2 (they need the `description` column/field). Does not block US3/US4 code, but T004 (migration applied) is required for any DB-backed test run.
- **US1 (Phase 3)**: After Foundational.
- **US2 (Phase 4)**: After Foundational; shares `schedule.py` and `schedule_service.py` with US1, so sequence US1 → US2 to avoid same-file conflicts.
- **US3 (Phase 5)**: After Foundational. Touches `schedule_repository.py`, `schedule_service.py`, `schedules.py` + a new dependency file.
- **US4 (Phase 6)**: After Foundational. Touches `schedule.py` and `schedule_service.py`.
- **Polish (Phase 7)**: After all targeted stories.

### Within Each User Story

- Tests first (must FAIL before implementation) per constitution.
- Model → schema → repository → service → endpoint.
- Same-file tasks run sequentially.

### File-contention notes (cross-story)

- `src/lib/schemas/schedule.py`: T008 (US1), T013 (US2), T024 (US4) — sequential.
- `src/lib/services/schedule_service.py`: T010 (US1), T014 (US2), T020 (US3), T025 (US4) — sequential.
- `src/lib/repositories/schedule_repository.py`: T009 (US1), T019 (US3) — sequential.
- `tests/api/v1/endpoints/test_schedules.py`: T007, T012, T017, T023 — sequential (same test file).

### Parallel Opportunities

- Test-writing tasks marked [P] within a story touch distinct test files and can be authored together (except the shared `test_schedules.py`, noted above).
- T018 (new `name_filter.py`) is fully parallel to other US3 tasks.
- US3 and US4 are logically independent and could be developed in parallel by different developers once Foundational is done (mind the shared `schedule_service.py` — coordinate T020 and T025).

---

## Implementation Strategy

### MVP First (Description feature = US1 + US2)

1. Phase 1 Setup → Phase 2 Foundational (migration + model).
2. Phase 3 US1 (persist + validate) → Phase 4 US2 (expose on details + list).
3. **STOP and VALIDATE**: quickstart Scenarios A–C, F(desc), G. This is a shippable increment.

### Incremental Delivery

4. Add US3 (name filter) → validate Scenarios D, E.
5. Add US4 (trigger on list) → validate Scenario F(trigger).
6. Phase 7 polish + full quality gates.

---

## Notes

- [P] = different files, no dependencies.
- Constitution requires: tests fail first; then `pytest`, `ruff`, `mypy` all green; then code-review-agent loop (handled in the `/speckit-implement` SDLC), then integration tests.
- Description max length (1000) and case-insensitive substring name match are locked from spec Assumptions / research decisions.
- The list `trigger` reuses the details shape for guaranteed parity (research Decision 7).

---

description: "Task list for Schedule Metadata & Filtering"
---

# Tasks: Schedule Metadata & Filtering

**Input**: Design documents from `specs/019-schedule-metadata-filtering/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/schedules-metadata.md, quickstart.md

**Tests**: REQUIRED — the project constitution mandates Test-First (TDD, NON-NEGOTIABLE). Every story's tests are written and confirmed FAILING before its implementation tasks.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1, US2, US3 — maps to user stories in spec.md

## Path Conventions

Web service layout (per plan.md): `src/api/`, `src/lib/`, `migrations/versions/`, tests mirror `src/` under `tests/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the working environment for an existing project (no scaffolding required).

- [X] T001 Ensure dependencies and DB are ready on branch `019-schedule-metadata-filtering`: run `uv sync` and confirm PostgreSQL is reachable for Alembic and tests.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the `metadata` column and migration that every user story depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Add `schedule_metadata` attribute mapped to DB column `metadata` (JSONB, NOT NULL, default empty dict) on the `Schedule` model in src/lib/models/schedule.py — attribute name MUST be `schedule_metadata` (the name `metadata` is reserved by SQLAlchemy/SQLModel; see research.md R1), column name pinned to `"metadata"`.
- [X] T003 Generate the Alembic migration with `uv run alembic revision --autogenerate -m "add metadata to schedules"`, then edit it in migrations/versions/<new>_add_metadata_to_schedules.py to add the `metadata` JSONB column as NOT NULL with `server_default=sa.text("'{}'::jsonb")` and a GIN index `IX_schedules_metadata` (`postgresql_using="gin"`), with a matching `downgrade()` that drops the index and column.
- [X] T004 Apply and verify the migration: `uv run alembic upgrade head`, confirm the `metadata` column and GIN index exist and pre-existing rows backfilled to `{}` (FR-014); confirm `uv run alembic downgrade -1` then `upgrade head` round-trips cleanly.

**Checkpoint**: Schema ready — user stories can begin.

---

## Phase 3: User Story 1 - Attach business metadata to a schedule (Priority: P1) 🎯 MVP

**Goal**: Users can supply a `metadata` map on create/update; it is persisted (replace semantics on update, empty default when omitted).

**Independent Test**: Create a schedule with `metadata` and confirm via repository/DB that the value is stored; create without metadata and confirm `{}`; update and confirm full replacement.

### Tests for User Story 1 (write first, confirm FAILING) ⚠️

- [X] T005 [P] [US1] Unit test: `ScheduleRequest` accepts a `metadata` object and defaults to `{}` when omitted, in tests/lib/schemas/test_schedule.py.
- [X] T006 [P] [US1] Unit test: `ScheduleService.create` persists `metadata`; `ScheduleService.update` fully replaces prior `metadata`; omitted metadata persists `{}`, in tests/lib/services/test_schedule_service.py.
- [X] T007 [P] [US1] Repository test: create/update round-trips `schedule_metadata` and a row created without metadata reads back `{}`, in tests/lib/repositories/test_schedule_repository.py.

### Implementation for User Story 1

- [X] T008 [P] [US1] Add `metadata: dict[str, Any]` field (default empty dict via `Field(default_factory=dict)`) to `ScheduleRequest` in src/lib/schemas/schedule.py.
- [X] T009 [US1] Add a keyword-only `metadata: dict[str, Any]` parameter to `ScheduleRepository.update` and assign it to `schedule.schedule_metadata` in src/lib/repositories/schedule_repository.py (depends on T002).
- [X] T010 [US1] In `ScheduleService.create`, set `schedule_metadata=request.metadata` on the `Schedule`; in `ScheduleService.update`, capture prior metadata for rollback and pass `request.metadata` to the repository update (and restore prior metadata in the compensating path), in src/lib/services/schedule_service.py (depends on T008, T009).

**Checkpoint**: Metadata persists correctly on create and update.

---

## Phase 4: User Story 2 - View a schedule's metadata in its details (Priority: P1)

**Goal**: The create/update/details responses include the full stored `metadata` object.

**Independent Test**: `GET /schedules/{id}` for a schedule with metadata returns the exact object; a schedule without metadata returns `{}`.

### Tests for User Story 2 (write first, confirm FAILING) ⚠️

- [X] T011 [P] [US2] Unit test: `ScheduleResponse` exposes `metadata` and `ScheduleService._to_response` maps `schedule.schedule_metadata` into it (including `{}`), in tests/lib/services/test_schedule_service.py.
- [X] T012 [P] [US2] Endpoint test: `GET /schedules/{id}` returns `metadata` in the response body (populated and empty cases), in tests/api/v1/endpoints/test_schedules.py.

### Implementation for User Story 2

- [X] T013 [P] [US2] Add `metadata: dict[str, Any]` field to `ScheduleResponse` in src/lib/schemas/schedule.py.
- [X] T014 [US2] Map `schedule.schedule_metadata` → `ScheduleResponse.metadata` in `ScheduleService._to_response` in src/lib/services/schedule_service.py (depends on T013).

**Checkpoint**: Details/create/update responses surface metadata; US1 + US2 deliver the create→read round trip (SC-001).

---

## Phase 5: User Story 3 - Filter the schedules list by metadata (Priority: P2)

**Goal**: `GET /schedules/?metadata__<key>=<value>` returns only owned schedules whose metadata contains all supplied pairs as exact string values (AND), preserving pagination.

**Independent Test**: With schedules of differing metadata, single- and multi-key filters return only matching rows; numeric-valued keys are not matched by string filters; unmatched keys return an empty page; no filter equals current behavior.

### Tests for User Story 3 (write first, confirm FAILING) ⚠️

- [X] T015 [P] [US3] Unit test: metadata-filter dependency parses `metadata__<key>` params (prefix stripping, key containing `__`, last-value-wins on duplicates, empty map when none present, ignores non-prefixed params), in tests/api/dependencies/test_metadata_filter.py.
- [X] T016 [P] [US3] Repository test: `list` with a containment filter — AND across keys, string-only matching (numeric stored value not matched), owner scoping, unmatched key → empty + correct `count`, and pagination over filtered results, in tests/lib/repositories/test_schedule_repository.py.
- [X] T017 [P] [US3] Endpoint test: `GET /schedules/?metadata__tenant=hcscd` filters; multi-filter AND; no-filter baseline unchanged, in tests/api/v1/endpoints/test_schedules.py.

### Implementation for User Story 3

- [X] T018 [P] [US3] Create a FastAPI dependency that reads `Request.query_params`, extracts `metadata__<key>` entries into a `dict[str, str]` (key = substring after first `metadata__`, last value wins), returning `{}` when none, in src/api/dependencies/metadata_filter.py.
- [X] T019 [US3] Add a keyword-only `metadata_filter: dict[str, str]` parameter to `ScheduleRepository.list`; when non-empty, apply `schedules.metadata @> cast(filter, JSONB)` to BOTH the count and page queries (SQLAlchemy comparator, no raw SQL); when empty, behavior is unchanged, in src/lib/repositories/schedule_repository.py (depends on T002).
- [X] T020 [US3] Thread the metadata filter through `ScheduleService.list` to the repository in src/lib/services/schedule_service.py (depends on T019).
- [X] T021 [US3] Inject the metadata-filter dependency into `list_schedules` and pass it to the service in src/api/v1/endpoints/schedules.py (depends on T018, T020).

**Checkpoint**: All three user stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation and quality gates across all stories.

- [X] T022 [P] Integration test for the full flow (create with metadata → GET details → list with `metadata__*` filter, including owner scoping and string-only matching) in tests/integration/test_schedule_metadata.py.
- [X] T023 Update the API examples/docstrings for the list endpoint to document `metadata__<key>` filtering in src/api/v1/endpoints/schedules.py, consistent with contracts/schedules-metadata.md.
- [X] T024 Run all quality gates and fix issues: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml`.
- [X] T025 Execute quickstart.md scenarios 1–10 against a running instance and confirm expected outcomes.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories (model + migration).
- **User Stories (Phase 3–5)**: All depend on Foundational completion.
  - US1 (P1) and US2 (P1) are closely related (both touch `schedule.py` schema and `schedule_service.py`); US2's response mapping is most useful once US1 persists data, so implement US1 → US2.
  - US3 (P2) depends only on Foundational; can proceed independently of US1/US2.
- **Polish (Phase 6)**: Depends on all targeted stories being complete.

### User Story Dependencies

- **US1 (P1)**: After Foundational. No dependency on other stories.
- **US2 (P1)**: After Foundational. Logically follows US1 (shares files; response shows what US1 persists).
- **US3 (P2)**: After Foundational. Independent of US1/US2.

### Within Each User Story

- Tests MUST be written and FAIL before implementation (TDD, constitution Principle III).
- Schema/model before services; services before endpoints; repository changes before service threading.

### Parallel Opportunities

- T005, T006, T007 (US1 tests) run in parallel — different files.
- T011, T012 (US2 tests) run in parallel — different files.
- T015, T016, T017 (US3 tests) run in parallel — different files.
- T018 (new dependency file) parallels T019 (repository) — different files.
- Across stories: once Foundational is done, US3 can be developed in parallel with US1/US2 by a separate developer.

---

## Parallel Example: User Story 3

```bash
# Write all US3 tests first (parallel), confirm FAILING:
Task: "Unit test for metadata-filter dependency in tests/api/dependencies/test_metadata_filter.py"
Task: "Repository containment-filter test in tests/lib/repositories/test_schedule_repository.py"
Task: "Endpoint filter test in tests/api/v1/endpoints/test_schedules.py"

# Then parallelizable implementation:
Task: "Create metadata-filter dependency in src/api/dependencies/metadata_filter.py"
Task: "Add containment filter to ScheduleRepository.list in src/lib/repositories/schedule_repository.py"
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2)

1. Phase 1: Setup.
2. Phase 2: Foundational (column + migration) — CRITICAL.
3. Phase 3 (US1) + Phase 4 (US2): metadata can be set and read back — this is the MVP (SC-001).
4. STOP and VALIDATE: create with metadata, GET details, confirm fidelity.

### Incremental Delivery

1. Setup + Foundational → schema ready.
2. US1 + US2 → set/read metadata → demo (MVP).
3. US3 → filter the list → demo.
4. Polish → integration test + quality gates + quickstart validation.

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks.
- US1 and US2 both edit src/lib/schemas/schedule.py and src/lib/services/schedule_service.py — sequence their edits to those shared files (not parallel across the two stories).
- Filtering uses JSONB `@>` containment, which is inherently type-aware (string-only matching, FR-009) and AND-composes (FR-008) — see research.md R2.
- Do not log metadata values (may contain business data) — constitution Principle II.
- Commit after each task or logical group.

---

description: "Task list for Multiple Cron Triggers per Schedule"
---

# Tasks: Multiple Cron Triggers per Schedule

**Input**: Design documents from `/specs/041-multi-cron-triggers/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/trigger-crons.md, quickstart.md

**Tests**: Included. The constitution makes TDD mandatory: within every phase, write the tests first and confirm they fail before the implementation tasks.

**Organization**: Grouped by user story. No Alembic migration is needed (`schedules.trigger` is JSONB, see data-model.md).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story the task belongs to (US1 to US4)

## Path Conventions

Single project with `src/api`, `src/worker`, `src/lib` and `tests/` mirroring `src/` (tests live under `tests/unit/lib/...`, `tests/unit/api/...`, `tests/integration/`).

---

## Phase 1: Setup

**Purpose**: Shared constant used by the schema and the tests.

- [X] T001 Add `MAX_CRON_ENTRIES = 20` to `src/lib/constants.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Extend the trigger schema so every story can submit a `crons` list. Blocks all user stories.

- [X] T002 Write failing unit tests in `tests/unit/lib/schemas/test_schedule_schemas.py` for `ScheduleTrigger`: `crons` accepted with 1 and 2 entries, exactly-one rule across `cron`/`crons`/`date` (each pair together rejected, none rejected, messages "only one trigger allowed" and "exactly one trigger required"), `trigger_type` is `cron` for a `crons` trigger
- [X] T003 Add `crons: list[CronTriggerSpec] | None` (default `None`, `min_length=1`, `max_length=MAX_CRON_ENTRIES`, description stating it fires whenever any entry fires, UTC, for different times on different days) to `ScheduleTrigger` in `src/lib/schemas/schedule.py`
- [X] T004 Update `ScheduleTrigger.validate_exactly_one` and `trigger_type` in `src/lib/schemas/schedule.py` to count `cron`, `crons` and `date`, returning `TriggerType.CRON` for `cron` or `crons`

**Checkpoint**: T002 tests pass; `ScheduleTrigger` accepts and validates the list form.

---

## Phase 3: User Story 1 - Run one schedule at several recurring times (Priority: P1) MVP

**Goal**: A schedule created with a `crons` list fires on the union of its entries, runs once for coinciding times, and reports the earliest upcoming `next_run_time`.

**Independent Test**: Create a schedule with Monday 08:00 and Friday 10:00 UTC entries; verify fire times and `next_run_time` (quickstart steps 1 to 2 and 7).

### Tests for User Story 1 (write first, confirm failing)

- [X] T005 [P] [US1] Add factory tests in `tests/unit/lib/services/test_trigger_factory.py`: two entries return `OrTrigger` with two UTC `CronTrigger` children; one-entry list returns a plain `CronTrigger`; from a fixed `now` of Friday 11:00 UTC the next fire time is Monday 08:00 UTC and from Monday 07:00 it is Monday 08:00, then Friday 10:00 after it; stepping through a four-week window yields exactly 8 fire times; identical or coinciding entries yield one fire time; one expired entry still fires on the other; all entries expired returns `None`; the `OrTrigger` pickles and unpickles with the same behaviour
- [X] T006 [P] [US1] Add service tests in `tests/unit/lib/services/test_schedule_service.py`: `create` with a `crons` trigger calls `add_job` once with `id=schedule_id` and an `OrTrigger`, and stores the dumped trigger containing `crons`
- [ ] T007 [P] [US1] Add integration test in `tests/integration/test_schedules_api.py`: POST a two-entry `crons` schedule, GET it back with `trigger.crons` as submitted, assert the registered trigger's next fire time is the earliest upcoming entry (the scheduler is mocked, so the response `next_run_time` is always null), and assert the list endpoint reports `trigger_type` `cron`; also POST a schedule whose two entries share a fire time and assert that `next_run_time` is that single instant and the job is registered exactly once

### Implementation for User Story 1

- [X] T008 [US1] Refactor `build_trigger` in `src/lib/services/trigger_factory.py`: extract a private helper building a UTC `CronTrigger` from one `CronTriggerSpec`; return it for `cron`, the single child for a one-entry `crons`, and `OrTrigger` of the children for two or more
- [X] T009 [US1] Add `trigger_kind` and `cron_entries` fields to the "Schedule job registered" and "Schedule job re-registered" log calls in `src/lib/services/schedule_service.py`, without logging any payload or credentials
- [X] T010 [P] [US1] Add a multi-cron example (Monday 08:00 and Friday 10:00) to `_SCHEDULE_EXAMPLES` in `src/api/v1/endpoints/schedules.py` and make sure the existing example-rendering test in `tests/unit/api/v1/endpoints/test_schedules.py` covers it

**Checkpoint**: US1 tests pass; a multi-cron schedule can be created and fires as specified.

---

## Phase 4: User Story 2 - Manage a multi-cron schedule like any other (Priority: P2)

**Goal**: Update, pause, resume and delete behave for list schedules as for single-cron ones, and the trigger can switch between `cron`, `crons` and `date`.

**Independent Test**: Quickstart steps 3 and 4.

### Tests for User Story 2 (write first)

- [X] T011 [P] [US2] Add service tests in `tests/unit/lib/services/test_schedule_service.py`: `update` switching `cron` to `crons`, `crons` to `date` and `crons` back to `cron` re-registers one job under the same id with the new trigger only; a failed re-registration restores the prior trigger dict (including a legacy dict without `crons`)
- [ ] T012 [P] [US2] Add integration tests in `tests/integration/test_schedules_api.py`: PUT switches trigger kinds; pause and resume keep the stored `crons` trigger and succeed; delete removes the single job (the scheduler is mocked, so `next_run_time` is not asserted)

### Implementation for User Story 2

- [ ] T013 [US2] Run T011 and T012; if pause, resume or restore paths fail for the `OrTrigger`, fix them in `src/lib/services/schedule_service.py` (none is expected, since the job id is unchanged)

**Checkpoint**: lifecycle operations verified for list schedules.

---

## Phase 5: User Story 3 - Existing schedules and clients keep working (Priority: P2)

**Goal**: Single-cron requests and stored rows without a `crons` key behave exactly as before.

**Independent Test**: Quickstart step 6.

### Tests for User Story 3 (write first)

- [X] T014 [P] [US3] Add schema tests in `tests/unit/lib/schemas/test_schedule_schemas.py`: a stored legacy trigger dict `{"cron": {...}, "date": null}` without `crons` validates and has `trigger_type` `cron`; a single-cron request dumps and re-validates unchanged
- [ ] T015 [P] [US3] Add integration test in `tests/integration/test_schedules_api.py`: insert a schedule row whose `trigger` JSON lacks `crons`, then GET, list and update it; behaviour matches a single-cron schedule, the returned `trigger` has the same `cron` content whether `crons` is absent or `null`, and a one-entry `crons` list fires like the equivalent single `cron`

### Implementation for User Story 3

- [ ] T016 [US3] Run T014 and T015 against the existing code; fix any regression in `src/lib/schemas/schedule.py` or `src/lib/services/schedule_service.py` (none is expected)

**Checkpoint**: backward compatibility proven by tests, with no data migration.

---

## Phase 6: User Story 4 - Clear feedback on invalid triggers (Priority: P3)

**Goal**: Invalid trigger submissions are rejected with an error that names the offending location.

**Independent Test**: Quickstart step 5.

### Tests for User Story 4 (write first)

- [X] T017 [P] [US4] Add schema tests in `tests/unit/lib/schemas/test_schedule_schemas.py`: empty `crons` rejected; `MAX_CRON_ENTRIES` accepted and `MAX_CRON_ENTRIES + 1` rejected; an invalid field in the second entry produces an error whose location is `("crons", 1, ...)`
- [ ] T018 [P] [US4] Add integration test in `tests/integration/test_schedules_api.py`: POST with `crons: []`, 21 entries, `cron` plus `crons`, and an invalid second entry each return 422 with `trigger.crons`-prefixed locations where applicable

### Implementation for User Story 4

- [ ] T019 [US4] Run T017 and T018; adjust the `crons` field constraints in `src/lib/schemas/schedule.py` if any case does not produce the expected location (none is expected, since Pydantic reports list indexes)

**Checkpoint**: all validation paths covered.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T020 [P] Update `documentation/api_contract.md`: add the `crons` field to the request shape, the multi-cron example, the response notes (absent `crons` equals null, `trigger_type` is `cron`) and the 422 error table from `contracts/trigger-crons.md`
- [X] T021 [P] Check `documentation/db_tables.md` and `documentation/requirements.md` for trigger descriptions that need the list form; update only if they describe trigger shape
- [X] T022 [P] Add an entry under `[Unreleased]` > `Added` in `CHANGELOG.md` describing `trigger.crons`
- [X] T023 Add an MCP check in `tests/unit/api/mcp/test_mcp_tools.py` that the create and update tool input schemas expose `trigger.crons` with its description
- [X] T024 Run `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src` and `uv run pytest --cov=src`; fix any findings
- [ ] T025 Walk through `specs/041-multi-cron-triggers/quickstart.md` end to end, then spawn `code-review-agent` per the constitution workflow and address its report

---

## Dependencies & Execution Order

- Phase 1 then Phase 2, which block everything else.
- US1 (Phase 3) is the MVP. US2, US3 and US4 depend only on Phase 2 plus the US1 factory change (T008), because their tests exercise the list form through registration.
- US2, US3 and US4 are independent of each other once T008 is done.
- Polish follows the stories. T020 to T022 can start once the contract is stable (after Phase 2).
- Within each story: tests, confirm failing, then implementation.

## Parallel Opportunities

- US1 tests T005, T006, T007 run in parallel (different files); T010 is parallel with T008 and T009.
- After T008: T011 and T012, T014 and T015, and T017 and T018 can be written in parallel across stories.
- Polish docs T020, T021 and T022 are parallel.

## Implementation Strategy

- **MVP**: Phases 1 to 3. This delivers creating and firing multi-cron schedules.
- Then US2 and US3 to prove lifecycle and compatibility, then US4, then Polish.
- Stories 2 to 4 are mostly verification, because the schema and the `OrTrigger` design already provide their behaviour; implementation tasks T013, T016 and T019 exist only to fix failures the tests reveal.

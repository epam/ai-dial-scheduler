---

description: "Task list for Per-Schedule Timezone"
---

# Tasks: Per-Schedule Timezone

**Input**: Design documents from `/specs/042-schedule-timezone/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/schedule-timezone.md, quickstart.md

**Tests**: Included. The constitution makes TDD mandatory: within every phase, write the tests first and confirm they fail before the implementation tasks.

**Organization**: Grouped by user story. Storage, the request field and the response fields are shared by every story, so they sit in the Foundational phase. One Alembic migration adds `schedules.timezone` (see data-model.md).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story the task belongs to (US1 to US5)

## Path Conventions

Single project with `src/api`, `src/worker`, `src/lib`, and `tests/` mirroring `src/` (`tests/unit/lib/...`, `tests/unit/api/...`, `tests/unit/migrations/`, `tests/integration/`). In integration tests APScheduler is mocked: `next_run_time` is always `null`, and fire times are asserted on the trigger passed to `mock_scheduler.add_job`.

---

## Phase 1: Setup

**Purpose**: The dependency, the constant and the test fixture shared by all phases.

- [X] T001 Add `tzdata>=2026.2` to `[project] dependencies` with `uv add "tzdata>=2026.2"`, updating `pyproject.toml` and `uv.lock`. Remember the macOS openssl `LDFLAGS` note if `uv sync` fails on psycopg2.
- [X] T002 [P] Add `DEFAULT_SCHEDULE_TIMEZONE = "UTC"` to `src/lib/constants.py`
- [X] T003 [P] Add `tests/helpers/browser_timezones.json` (418 names, the reference `Intl.supportedValuesOf('timeZone')` list; the only copy, test-only)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Store, accept, validate and return `timezone`. Blocks all user stories.

### Tests (write first, confirm failing)

- [X] T004 [P] Write `tests/unit/migrations/test_add_timezone.py` in the pattern of `test_add_is_deleted.py`. It loads the migration from its file and patches `op`, then asserts:
  - `down_revision == "b7c5e6f8a9d0"`;
  - `upgrade` issues exactly one `add_column("schedules", ...)` whose column is `timezone`, `String(64)`, `nullable=False`, with server default `'UTC'`;
  - `downgrade` issues `drop_column("schedules", "timezone")`.
- [X] T005 [P] Add schema tests to `tests/unit/lib/schemas/test_schedule_schemas.py`:
  - `ScheduleRequest` without `timezone`, and with `timezone=None`, gives `"UTC"`;
  - `"Europe/Berlin"` is kept unchanged;
  - `"Mars/Olympus"` is rejected with an error at location `("timezone",)`;
  - `ScheduleResponse` and `ScheduleListItem` fail to validate without `timezone`.
- [X] T006 [P] Add tests to `tests/unit/lib/repositories/test_schedule_repository.py`: `update` assigns the passed `timezone` to the row, and calling it without `timezone` is a `TypeError` (required keyword).
- [X] T007 [P] Add service tests to `tests/unit/lib/services/test_schedule_service.py`:
  - `create` builds the `Schedule` row with `timezone=request.timezone`;
  - `_to_response` and the list items carry `schedule.timezone`;
  - `update` passes `timezone=request.timezone` to the repository;
  - a failed re-registration calls the restore `update` with the prior `timezone`.

### Implementation

- [X] T008 Create `migrations/versions/20261104_1200_<rev>_add_timezone_to_schedules.py`, which revises `b7c5e6f8a9d0`. Upgrade: `op.add_column("schedules", sa.Column("timezone", sa.String(64), nullable=False, server_default=sa.text("'UTC'")))`. Downgrade: `drop_column`. Use literals only, no `src` imports, and a docstring explaining the same-statement backfill, as in `add_is_deleted_to_schedules`.
- [X] T009 Add a `timezone: str` column to `Schedule` in `src/lib/models/schedule.py`: `Column("timezone", String(64), nullable=False, server_default=text("'UTC'"))`, default `DEFAULT_SCHEDULE_TIMEZONE`, plus an Attributes docstring entry.
- [X] T010 In `src/lib/schemas/schedule.py`:
  - add a module-private `@functools.cache` function `_known_timezones() -> frozenset[str]` returning `frozenset(zoneinfo.available_timezones())`;
  - add `ScheduleRequest.timezone: str = Field(default=DEFAULT_SCHEDULE_TIMEZONE, description=...)`. The description gives the IANA format, the `UTC` default, case sensitivity and that browser values are accepted, and says cron times and naive datetimes are local to this zone;
  - add a `mode="before"` validator turning `None` into the default;
  - add an after-validator raising `unknown timezone {v!r}; expected an IANA identifier such as 'Europe/Berlin'`;
  - add a required `timezone: str` to `ScheduleResponse` and `ScheduleListItem`.
- [X] T011 Add a required keyword-only `timezone: str` to `ScheduleRepository.update` in `src/lib/repositories/schedule_repository.py`, always assigned, and update its docstring.
- [X] T012 In `src/lib/services/schedule_service.py`:
  - `create` sets `Schedule(timezone=request.timezone)`;
  - `update` captures `prior_timezone`, passes `timezone=request.timezone`, and passes `timezone=prior_timezone` on restore;
  - `_to_response` and the `ScheduleListItem` mapping in `list` add `timezone=schedule.timezone`.

**Checkpoint**: T004 to T007 pass. `uv run alembic upgrade head` and `downgrade -1` both work against a local database. The API accepts, stores and returns `timezone`, but triggers are still UTC.

---

## Phase 3: User Story 1 - Schedule runs at local wall-clock time (Priority: P1) MVP

**Goal**: Every trigger is evaluated in the schedule's zone, so runs happen at the configured local time across daylight-saving changes.

**Independent Test**: Quickstart step 1. A Berlin 09:00 schedule's registered trigger fires at 08:00Z in winter and 07:00Z in summer.

### Tests for User Story 1 (write first, confirm failing)

- [X] T013 [P] [US1] Add factory tests to `tests/unit/lib/services/test_trigger_factory.py`, using a fixed `now`:
  - for `cron`, one-entry `crons`, two-entry `crons` and `date`, every built trigger (and every `OrTrigger` child) has `timezone == ZoneInfo(<zone>)`;
  - `Europe/Berlin` daily 09:00 fires at 08:00Z on 2027-03-26 and 07:00Z on 2027-03-29;
  - `Asia/Kolkata` 09:00 fires at 03:30Z;
  - `America/New_York` with Monday 08:00 and Friday 10:00 fires at those local times;
  - a naive `start_date` and a naive `date` are localised in the zone, and aware ones keep their instant;
  - with `"UTC"`, fire times over a four-week window equal those of a plain UTC `CronTrigger` (regression).
- [X] T014 [P] [US1] Add schema tests to `tests/unit/lib/schemas/test_schedule_schemas.py`: `CronTriggerSpec.trigger_kwargs()` uses `UTC`, and `trigger_kwargs(ZoneInfo("Asia/Tokyo"))` carries Tokyo.
- [X] T015 [P] [US1] Add service tests to `tests/unit/lib/services/test_schedule_service.py`:
  - `create` and `update` call `add_job` with a trigger whose `timezone` is `ZoneInfo(request.timezone)`;
  - "Schedule job registered" and "Schedule job re-registered" log entries include `timezone`.
- [X] T016 [P] [US1] Add an integration test to `tests/integration/test_schedules_api.py`: POST a weekdays 09:00 `Europe/Berlin` cron schedule; the trigger passed to `mock_scheduler.add_job` has the Berlin zone, and its `get_next_fire_time(None, <fixed now>)` is 09:00 Berlin.

### Implementation for User Story 1

- [X] T017 [US1] Add a `timezone: tzinfo = UTC` parameter to `CronTriggerSpec.trigger_kwargs` in `src/lib/schemas/schedule.py`. The field validator keeps calling it without an argument.
- [X] T018 [US1] Change `build_trigger(trigger, timezone: str)` in `src/lib/services/trigger_factory.py`: resolve `ZoneInfo(timezone)` once, pass it to every `CronTrigger`/`EntryCronTrigger` through `trigger_kwargs(tz)` and to `DateTrigger(timezone=tz)`, and update the docstrings, which still say "anchored to UTC".
- [X] T019 [US1] In `src/lib/services/schedule_service.py`:
  - change `_register_job(schedule_id, request: ScheduleRequest, properties)` to call `build_trigger(request.trigger, request.timezone)`;
  - change `_trigger_log_fields(request)` to add `"timezone": request.timezone`;
  - update the call sites in `create` and `update`.
- [X] T020 [P] [US1] In `src/api/v1/endpoints/schedules.py`, add a `with_timezone` example (weekdays 09:00 `Europe/Berlin`) to `_SCHEDULE_EXAMPLES`, and remove "UTC" from the `multiple_crons` summary. In `src/lib/schemas/schedule.py`, replace "All times are UTC." in the `ScheduleTrigger.crons` description with "Times are local to the schedule's `timezone`." Make sure the example-rendering test in `tests/unit/api/v1/endpoints/test_schedules.py` covers the new example.

**Checkpoint**: T013 to T016 pass. Schedules fire in their zone, but a repeated local time still fires twice in autumn.

---

## Phase 4: User Story 1 (continued) - Daylight-saving correctness (Priority: P1)

**Goal**: FR-012 and FR-013. A missing local time fires once, shifted forward. A repeated specific local time fires once. Every-hour entries fire every real hour.

**Independent Test**: Quickstart section 3.

### Tests (write first, confirm failing)

- [X] T021 [P] [US1] Add DST tests to `tests/unit/lib/services/test_trigger_factory.py` for `Europe/Berlin`, stepping fire times with `get_next_fire_time(prev, prev)`:
  - **spring**: daily 02:30 fires once on 2027-03-28, at 01:30Z;
  - **autumn, daily**: daily 02:30 fires once on 2027-10-31, at 00:30Z, and next on 2027-11-01 at 01:30Z;
  - **autumn, hourly**: hourly (`minute=0`) fires at 00:00Z, 01:00Z and 02:00Z, so both 02:00 locals;
  - **default hour**: `day=1` with the default hour `0` is not treated as every hour;
  - **narrow hour range**: `hour="1-3", minute="*/30"` skips the second 02:00 and 02:30;
  - **`OrTrigger`**: a two-entry trigger (daily 02:30 and Monday 08:00) gives one 02:30;
  - **pickling**: a `WallClockCronTrigger` and a two-entry `OrTrigger` in `Europe/Kiev` survive a pickle round-trip, keeping the class and the zone key;
  - **regression**: the existing `TestBuildTriggerCronsStartDate` tests still pass.

### Implementation

- [X] T022 [US1] Add `WallClockCronTrigger(CronTrigger)` to `src/lib/services/trigger_factory.py`. *(Implemented first as described below, then replaced after code review by wall-clock stepping plus `InstantOrTrigger`; see research R5a.)* Its `get_next_fire_time` loops `candidate = super().get_next_fire_time(candidate, candidate)` while all of these hold:
  - the candidate is not `None`;
  - `candidate.fold == 1`;
  - the two folds have different UTC offsets;
  - `str(<hour field>) != "*"`.

  Make `EntryCronTrigger` subclass it. Use `WallClockCronTrigger` for `cron` and for one-entry `crons` in `build_trigger`. Both docstrings must state that the class path is pickled into job rows and must stay stable, and why `is_default` is not used (research R5).

**Checkpoint**: T021 passes. All US1 acceptance scenarios and the DST edge cases are covered.

---

## Phase 5: User Story 2 - Frontend round-trips the timezone unchanged (Priority: P1)

**Goal**: Every browser value and `UTC` is accepted and returned exactly as sent, everywhere a schedule is returned.

**Independent Test**: Quickstart steps 2 and 3.

### Tests for User Story 2 (write first)

- [X] T023 [P] [US2] Add a parametrised schema test to `tests/unit/lib/schemas/test_schedule_schemas.py`. Every name in `tests/helpers/browser_timezones.json`, plus `UTC`, `Etc/UTC` and `Europe/Kyiv`, validates and is returned unchanged by `ScheduleRequest` (SC-002). Assert that the fixture has 418 entries, so it cannot be truncated silently.
- [X] T024 [P] [US2] Add an integration test to `tests/integration/test_schedules_api.py`: POST with `Europe/Kiev`; the create response, `GET /v1/schedules/{id}`, the matching list item and the pause and resume responses all return `"timezone": "Europe/Kiev"`.
- [X] T025 [P] [US2] Add an endpoint test to `tests/unit/api/v1/endpoints/test_schedules.py`: the OpenAPI schema exposes `timezone` on the request (with its description and default) and on both response models.

### Implementation for User Story 2

- [X] T026 [US2] Run T023 to T025. Fix any failure in `src/lib/schemas/schedule.py` or `src/lib/services/schedule_service.py` (none is expected after Phase 2).

**Checkpoint**: The frontend contract is proven.

---

## Phase 6: User Story 3 - Change a schedule's timezone (Priority: P2)

**Goal**: An update replaces the zone and re-registers the trigger in it. An omitted zone resets to UTC.

**Independent Test**: Quickstart steps 4 to 6.

### Tests for User Story 3 (write first)

- [X] T027 [P] [US3] Add integration tests to `tests/integration/test_schedules_api.py`:
  - PUT a UTC schedule with `Asia/Tokyo`: the stored and returned value is `Asia/Tokyo`, and the second `add_job` call's trigger has the Tokyo zone;
  - PUT again without `timezone`: the stored value is `UTC`;
  - pause a Berlin schedule, then resume it: the stored zone is unchanged;
  - PUT with a new zone while `add_job` raises: the response is 502 and the row still has the prior zone.

### Implementation for User Story 3

- [X] T028 [US3] Run T027. Fix any failure in `src/lib/services/schedule_service.py` (none is expected after Phase 2 and T019).

**Checkpoint**: Editing the zone is proven, including compensation.

---

## Phase 7: User Story 4 - Existing schedules and clients keep working (Priority: P2)

**Goal**: Rows created before the migration and requests without `timezone` behave exactly as before.

**Independent Test**: Quickstart steps 8 and 9.

### Tests for User Story 4 (write first)

- [X] T029 [P] [US4] Add an integration test to `tests/integration/test_schedule_repository.py`: insert a `schedules` row without the `timezone` column. The server default fills `UTC`, and `get` and `list` return it.
- [X] T030 [P] [US4] Add integration tests to `tests/integration/test_schedules_api.py`: POST without `timezone`, and with `"timezone": "UTC"`, returns `"UTC"` and registers a UTC trigger; a request body copied from an existing e2e file (`tests/e2e/1.1-*.http`) is accepted unchanged.

### Implementation for User Story 4

- [X] T031 [US4] Run T029 and T030. Fix any regression (none is expected).

**Checkpoint**: Backward compatibility proven. Existing pickled UTC jobs are not touched (research R7).

---

## Phase 8: User Story 5 - Clear feedback on an invalid timezone (Priority: P3)

**Goal**: Invalid values are rejected at `body.timezone`, and nothing is stored or scheduled.

**Independent Test**: Quickstart step 7.

### Tests for User Story 5 (write first)

- [X] T032 [P] [US5] Add parametrised schema tests to `tests/unit/lib/schemas/test_schedule_schemas.py`. `europe/berlin`, `+02:00`, `""`, `Mars/Olympus` and `120` are rejected at location `("timezone",)`. The first four carry the contract message.
- [X] T033 [P] [US5] Add integration tests to `tests/integration/test_schedules_api.py`:
  - POST with each invalid value returns 422 with `loc == ["body", "timezone"]`, no row is created, and `add_job` is not called;
  - PUT with an invalid value returns 422, and the stored schedule (zone and trigger) is unchanged.

### Implementation for User Story 5

- [X] T034 [US5] Run T032 and T033. Adjust the validators in `src/lib/schemas/schedule.py` if a location or message differs (none is expected).

**Checkpoint**: All validation paths covered.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T035 [P] Update `documentation/api_contract.md`:
  - add `timezone` to the create/update request, detail response and list item shapes;
  - add a "Timezone" paragraph covering the default, accepted values, `Etc/GMT±N` sign inversion, PUT resetting an omitted zone to UTC, local cron times and naive datetimes, the DST rules, and timestamps staying UTC;
  - add the 422 row;
  - replace "All times are UTC".
- [X] T036 [P] Add the `timezone` column (`VARCHAR(64) NOT NULL DEFAULT 'UTC'`, IANA name stored as submitted) to `documentation/db_tables.md`.
- [X] T037 [P] Update `CHANGELOG.md` under `[Unreleased]`:
  - Added: `timezone` on schedules;
  - Changed: a repeated local time fires once when clocks go back;
  - Changed: `tzdata` is now a runtime dependency.
- [X] T038 [P] Add `tests/e2e/1.5-Create-schedule-with-timezone.http`: create with `Europe/Berlin`, GET, PUT to `Asia/Tokyo`, and an invalid `europe/berlin` that expects 422.
- [X] T039 Add a test to `tests/unit/api/mcp/test_mcp_tools.py`: the create and update tool input schemas expose `timezone` with its description, and the `crons` description no longer says UTC.
- [X] T040 Against a migrated database, run `uv run alembic revision --autogenerate -m "check"`, confirm it produces an empty migration, and delete that file.
- [X] T041 Run `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src` and `uv run pytest --cov=src`, and fix any findings.
- [ ] T042 Walk through `specs/042-schedule-timezone/quickstart.md` end to end. Then spawn `code-review-agent` per the constitution workflow, address its report, and repeat until no issues remain.

---

## Dependencies & Execution Order

- Phase 1, then Phase 2. Both block everything else. T001 must come before T010, because validation relies on `tzdata` being present everywhere.
- **US1** (Phases 3 and 4) is the MVP and depends only on Phase 2. Phase 4 depends on T018.
- **US2, US4 and US5** depend only on Phase 2 and are independent of each other and of US1.
- **US3** depends on Phase 2 and T019, because it asserts the zone of the re-registered trigger.
- **Polish**: T035 to T038 can start once the contract is stable (after Phase 2). T039 to T042 come last.
- **Within each phase**: write the tests, confirm they fail, then implement.

## Parallel Opportunities

- **Setup**: T002 and T003.
- **Phase 2 tests**: T004 to T007 (different files). T008 and T009 can be written in parallel with T010 and T011.
- **US1 tests**: T013 to T016. T020 runs in parallel with T017 to T019.
- **After Phase 2**: the test tasks of US2 (T023 to T025), US4 (T029 and T030) and US5 (T032 and T033) can be written in parallel with US1.
- **Docs**: T035 to T038.

## Implementation Strategy

- **MVP**: Phases 1 to 4. A zone can be stored and returned, and schedules fire correctly in it, including across DST changes.
- **Then**: US2 to prove the frontend contract, then US3 and US4, then US5, then Polish.
- **Mostly verification**: US2 to US5 add tests on top of the Phase 2 and Phase 3 code. T026, T028, T031 and T034 exist only to fix failures the tests reveal.

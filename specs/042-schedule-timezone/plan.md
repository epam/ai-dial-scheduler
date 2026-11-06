# Implementation Plan: Per-Schedule Timezone

**Branch**: `042-schedule-timezone` | **Date**: 2026-11-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/042-schedule-timezone/spec.md`

## Summary

Add an optional top-level `timezone` field to the schedule create and update request. Store it in a new `schedules.timezone` column (`VARCHAR(64) NOT NULL DEFAULT 'UTC'`), return it in every schedule response and list item, and evaluate all triggers in that zone. Validation is case-sensitive membership in the IANA database shipped by the `tzdata` package, so every browser value and `UTC` is accepted. The trigger factory passes `ZoneInfo(timezone)` to every APScheduler trigger. A new `WallClockCronTrigger` removes the duplicate run that APScheduler 3.11 produces for a repeated local time when the clocks go back. Existing rows are backfilled to `UTC` by the column default and keep firing at the same instants.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, Pydantic v2, APScheduler 3.11, SQLModel, Alembic. New direct dependency `tzdata>=2026.2`.

**Storage**: PostgreSQL. One new column on `schedules` through one Alembic migration.

**Testing**: pytest, pytest-asyncio, pytest-mock

**Target Platform**: Linux containers (API service and worker, same image)

**Project Type**: web-service with background worker

**Performance Goals**: N/A. The timezone set is computed once per process. The fall-back skip loops at most once per evaluation.

**Constraints**: Backward compatible with existing requests, stored rows and pickled jobs. Validation must give the same result on macOS and Linux.

**Scale/Scope**: About 7 source files, 1 migration, 1 dependency, plus docs and tests.

## Constitution Check

| Gate | Status | Notes |
|------|--------|-------|
| I. Reliability First | Pass | Invalid zones are rejected before anything is written. Create rollback and update restore now cover `timezone`. The fall-back duplicate run is removed (R5). `tzdata` makes zone resolution independent of the base image (R3). Registration still runs in `asyncio.to_thread`. |
| II. Observability | Pass | `timezone` is added to the "Schedule job registered" and "Schedule job re-registered" log entries. It is not sensitive. Run persistence is unchanged. |
| III. Test-First | Pass | The test plan below is written before implementation. |
| IV. Contract-Driven API | Pass | Additive OpenAPI change, see [contracts/schedule-timezone.md](contracts/schedule-timezone.md). No major bump. The API/worker boundary is unchanged: the worker receives the same job kwargs, and the zone travels inside the pickled trigger. |
| V. Simplicity & YAGNI | Pass with one note | One trigger subclass, needed for FR-013. No setting and no deny-list. See Complexity Tracking for the repository signature. |
| Technical constraints | Pass | No raw SQL. Autogenerate-compatible migration. `__init__.py` untouched. Absolute imports. `build_trigger` gets 2 parameters. |

Re-checked after design: still passes.

## Project Structure

### Documentation (this feature)

```text
specs/042-schedule-timezone/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/requirements.md
├── contracts/
│   └── schedule-timezone.md
└── tasks.md                     # created by /speckit-tasks
```

### Source Code (repository root)

```text
pyproject.toml, uv.lock                     # + tzdata>=2026.2
migrations/versions/20261104_1200_<rev>_add_timezone_to_schedules.py

src/lib/
├── constants.py                            # DEFAULT_SCHEDULE_TIMEZONE = "UTC"
├── models/schedule.py                      # timezone column
├── schemas/schedule.py                     # ScheduleRequest/Response/ListItem.timezone, validators,
│                                           # trigger_kwargs(timezone), crons description
├── repositories/schedule_repository.py     # update(..., timezone=...)
└── services/
    ├── trigger_factory.py                  # build_trigger(trigger, timezone), WallClockCronTrigger
    └── schedule_service.py                 # persist, restore, respond and log timezone
src/api/v1/endpoints/schedules.py           # OpenAPI example "with_timezone"

tests/helpers/browser_timezones.json        # reference Intl.supportedValuesOf('timeZone') list, 418 names (test-only)
tests/unit/migrations/test_add_timezone.py
tests/unit/lib/schemas/test_schedule_schemas.py
tests/unit/lib/services/test_trigger_factory.py
tests/unit/lib/services/test_schedule_service.py
tests/unit/lib/repositories/test_schedule_repository.py
tests/unit/api/v1/endpoints/test_schedules.py
tests/integration/test_schedules_api.py
tests/integration/test_schedule_repository.py
tests/e2e/1.5-Create-schedule-with-timezone.http

documentation/api_contract.md
documentation/db_tables.md
CHANGELOG.md
```

**Structure Decision**: The existing `api/` + `worker/` + `lib/` layout. All logic lives in `lib/`. The worker needs no change because the zone is part of the pickled trigger (R7).

## Design Notes

1. **Schema** (`ScheduleRequest`):
   ```python
   timezone: str = Field(default=DEFAULT_SCHEDULE_TIMEZONE, description="IANA timezone ... default UTC ...")

   @field_validator("timezone", mode="before")
   def default_null_timezone(cls, v): return DEFAULT_SCHEDULE_TIMEZONE if v is None else v

   @field_validator("timezone")
   def validate_timezone(cls, v): if v not in _known_timezones(): raise ValueError(f"unknown timezone {v!r}; ...")
   ```
   `_known_timezones()` is a module-private `@functools.cache` function returning `frozenset(zoneinfo.available_timezones())`. This is a cached pure function, not a mutable global or a client. The description states the default, case sensitivity, that browser values are accepted, and that cron times are local to the zone. It becomes the MCP tool documentation.
2. **`CronTriggerSpec.trigger_kwargs(self, timezone: tzinfo = UTC)`**. The field validator keeps calling it without an argument, because field ranges do not depend on the zone.
3. **Factory**:
   - `build_trigger(trigger: ScheduleTrigger, timezone: str)` resolves `ZoneInfo(timezone)` once. It returns `WallClockCronTrigger` for `cron` and for a one-entry `crons`, `OrTrigger([EntryCronTrigger, ...])` for 2+ entries, and `DateTrigger(run_date=trigger.date, timezone=tz)` for `date`.
   - `WallClockCronTrigger.get_next_fire_time`: step a UTC copy of the fields through naive wall-clock time and map each match to instants per the DST rules (missing time → shifted instant; repeated time → first instant, plus the second only if the hour field matches all 24 hours); return the earliest instant at or after the lower bound, compared by timestamp. See research R5a, which replaced the original fold-skip loop after code review.
   - `InstantOrTrigger(OrTrigger)` picks the earliest child fire time by timestamp.
   - `EntryCronTrigger(WallClockCronTrigger)` keeps its `start_date` override. Both class names and the module path stay stable, because they are pickled. Note this in the docstrings, as `EntryCronTrigger` already does.
4. **Model and migration**: see [data-model.md](data-model.md). Write the migration by hand in the style of `add_is_deleted_to_schedules`, with literals and a docstring explaining the backfill. Check it with `alembic revision --autogenerate` producing no diff afterwards.
5. **Repository**: `update(..., timezone: str, ...)` as a required keyword-only argument that is always assigned. `create` takes the `Schedule` object, so it needs no change.
6. **Service**:
   - `create`: `Schedule(..., timezone=request.timezone)` and `_register_job(schedule_id, request, properties_dict)`. Pass the request so `_register_job` reaches both `trigger` and `timezone` without a 4th parameter.
   - `update`: capture `prior_timezone`, pass `timezone=request.timezone` to the repository, and pass `timezone=prior_timezone` in the restore call.
   - `_to_response` and the list-item mapping add `timezone=schedule.timezone`.
   - `_trigger_log_fields(request)` adds `"timezone": request.timezone`.
7. **OpenAPI example**: a `with_timezone` example (weekdays 09:00 `Europe/Berlin`), added to `_SCHEDULE_EXAMPLES`. Update the `crons` example summary so it does not say UTC.
8. **Docs**: follow the deliverables listed in the contract. Note that `documentation/requirements.md` line 50 already requires "a schedule definition with timezone", which this feature satisfies, so it needs no change.

## Test Plan (written first)

- **Migration** (`test_add_timezone.py`, same pattern as `test_add_is_deleted.py`): the revision chains to `b7c5e6f8a9d0`; upgrade issues one `add_column` that is `String(64)`, NOT NULL, with server default `'UTC'`; downgrade drops the column.
- **Schema**:
  - omitted and `null` give `"UTC"`;
  - each of the 418 names in `tests/helpers/browser_timezones.json`, plus `UTC`, `Etc/UTC` and `Europe/Kyiv`, is accepted and kept unchanged (parametrised, SC-002);
  - `Mars/Olympus`, `europe/berlin`, `+02:00`, `""` and `120` are rejected at the `timezone` location;
  - `trigger_kwargs()` defaults to UTC, and `trigger_kwargs(ZoneInfo("Asia/Tokyo"))` carries the zone;
  - the response and list-item models require `timezone`.
- **Factory**, with a fixed `now`:
  - every built trigger carries the zone;
  - Berlin daily 09:00 gives 08:00Z on 2027-03-26 and 07:00Z on 2027-03-29;
  - `Asia/Kolkata` 09:00 gives 03:30Z;
  - spring forward: 02:30 on 2027-03-28 fires once, at 01:30Z;
  - fall back: daily 02:30 on 2027-10-31 fires once, at 00:30Z, and the next fire is 2027-11-01;
  - hourly fires at both 02:00 occurrences;
  - `day=1` with the default hour 0 is not treated as every hour;
  - `hour="1-3", minute="*/30"` skips the second 02:00 and 02:30;
  - a two-entry `OrTrigger` in the zone de-duplicates the same way;
  - a naive `start_date` and a naive `date` are localised in the zone, and an aware one keeps its instant;
  - UTC triggers give the same fire times as before (regression);
  - pickle round-trip keeps the class and the zone key (`Europe/Kiev`);
  - the `EntryCronTrigger` start-date tests still pass.
- **Service**:
  - create persists the timezone and registers the trigger in that zone;
  - update replaces the timezone, and omitting it stores `UTC`;
  - a failed re-registration restores the prior timezone;
  - responses and list items carry the timezone;
  - registration logs include `timezone`.
- **Repository**: `update` assigns `timezone`, and the restore path writes the prior value.
- **Endpoint**: POST, PUT and GET echo `timezone`; 422 at `body.timezone` for invalid values, with nothing persisted; the OpenAPI schema exposes the field with its description.
- **Integration** (PostgreSQL; APScheduler is mocked, so `next_run_time` is always `null`, and fire times are asserted on the trigger passed to `add_job`): a row inserted without `timezone` reads back as `UTC`; create with `Europe/Kiev` round-trips unchanged; the registered trigger carries the zone and its next fire time from a fixed `now` is the local time; PUT changes the zone of the registered trigger; pause and resume keep the stored zone.

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|-----------|------------|--------------------------------------|
| `ScheduleRepository.update` grows from 7 to 8 keyword-only parameters (the constitution allows at most 3) | It is a pre-existing signature, and the restore path must write `timezone` together with the other columns | Moving it to a Pydantic "update values" model touches every caller and test of the repository, which is out of scope here. Worth doing as a separate refactor. |
| `WallClockCronTrigger` steps wall-clock time itself, plus `InstantOrTrigger` | APScheduler 3.11 cron stepping is not DST-safe: duplicates on fall-back, a skipped day after spring-forward, and `OrTrigger` misordering (R5, R5a). This violates FR-004, FR-012, FR-013 and SC-004 | Patching single cases left other cases broken (found by the R5a property check). APScheduler 4 is pre-release. |

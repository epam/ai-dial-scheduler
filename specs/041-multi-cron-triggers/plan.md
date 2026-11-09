# Implementation Plan: Multiple Cron Triggers per Schedule

**Branch**: `041-multi-cron-triggers` | **Date**: 2026-10-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/041-multi-cron-triggers/spec.md`

## Summary

Let a schedule's trigger carry a list of cron specifications. A new optional `crons` field on `ScheduleTrigger` sits beside `cron` and `date`, and exactly one of the three is allowed. At registration time the trigger factory builds one `CronTrigger` per entry and combines two or more with APScheduler's `OrTrigger`, so the schedule stays a single job under a single id. Because the trigger is already stored as JSONB and the job id does not change, create, update, pause, resume, delete and run history keep working without a migration.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, Pydantic v2, APScheduler 3.11 (`OrTrigger`), SQLModel

**Storage**: PostgreSQL; `schedules.trigger` JSONB, no schema change

**Testing**: pytest, pytest-asyncio, pytest-mock

**Target Platform**: Linux containers (API service and worker)

**Project Type**: web-service with background worker

**Performance Goals**: N/A. At most 20 child triggers are evaluated per fire-time computation.

**Constraints**: Backward compatible with existing requests and stored rows. UTC only. No Alembic migration.

**Scale/Scope**: Changes in 5 source files plus constants, docs and tests.

## Constitution Check

| Gate | Status | Notes |
|------|--------|-------|
| I. Reliability First | Pass | Registration failures already roll back on create and restore on update. Bounded list size (20) limits trigger evaluation cost. `OrTrigger` registration runs in the existing `asyncio.to_thread` call, so the event loop is not blocked. |
| II. Observability | Pass | Existing registration and run logging and run persistence are unchanged. Adds `trigger_kind` and `cron_entries` to the existing "Schedule job registered" log entries so multi-cron registrations are traceable. No secrets involved. |
| III. Test-First | Pass | Tests are listed per layer below and are written before implementation per the constitution workflow. |
| IV. Contract-Driven API | Pass | Additive change to the OpenAPI schema; see [contracts/trigger-crons.md](contracts/trigger-crons.md). No major bump. Internal API/worker boundary is unchanged: the worker receives the same job kwargs. |
| V. Simplicity & YAGNI | Pass | No new layers or abstractions. Reuses `CronTriggerSpec` per entry and the library's `OrTrigger`. No setting for the limit. |
| Technical constraints | Pass | No raw SQL, no migration needed (JSONB content only), `__init__.py` untouched, absolute imports, at most 3 parameters per function. |

Re-check after design: still passes. No Complexity Tracking entries.

## Project Structure

### Documentation (this feature)

```text
specs/041-multi-cron-triggers/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── trigger-crons.md
└── tasks.md             # created by /speckit-tasks
```

### Source Code (repository root)

```text
src/lib/
├── constants.py                      # MAX_CRON_ENTRIES = 20
├── schemas/schedule.py               # ScheduleTrigger.crons, exactly-one validator, trigger_type
└── services/
    ├── trigger_factory.py            # CronTrigger per entry; OrTrigger for 2+
    └── schedule_service.py           # log fields only; no logic change
src/api/v1/endpoints/schedules.py     # OpenAPI example for crons

tests/unit/lib/schemas/test_schedule_schemas.py
tests/unit/lib/services/test_trigger_factory.py
tests/unit/lib/services/test_schedule_service.py
tests/unit/api/v1/endpoints/test_schedules.py
tests/integration/test_schedules_api.py

documentation/api_contract.md
documentation/requirements.md         # if it states trigger rules
CHANGELOG.md                          # [Unreleased] > Added
```

**Structure Decision**: Existing `api/` + `worker/` + `lib/` layout. All logic lives in `lib/`; the worker is unaffected because it executes jobs by id and kwargs, not by trigger shape.

## Design Notes

1. **Schema**: `crons: list[CronTriggerSpec] | None = Field(default=None, min_length=1, max_length=MAX_CRON_ENTRIES, description=...)`. The description is part of the MCP tool-input schema and should state "fires whenever any entry fires; UTC; use for different times on different days".
2. **Validator**: replace the two-way check with a count of set fields among `cron`, `crons`, `date`; error messages stay "only one trigger allowed" / "exactly one trigger required".
3. **Factory**: extract a small private helper that builds a `CronTrigger` from one `CronTriggerSpec`; `build_trigger` returns it for `cron`, the single child for a one-entry list, `OrTrigger(children)` otherwise.
4. **Service**: `trigger.model_dump(mode="json")` and the `ScheduleTrigger.model_validate(schedule.trigger)` calls in `list` already handle the new field, so no logic change.
5. **Docs**: per the documentation memory note, update `api_contract.md`, which is hand-maintained. `db_tables.md` needs no change (no DB change), but its trigger JSON description should be checked.

## Test Plan (written first)

- **Schema**: valid list; empty list rejected; 21 entries rejected; `cron`+`crons`, `crons`+`date` and none rejected; error location names the failing entry; `trigger_type` is `cron` for a list; a legacy dict without `crons` validates.
- **Factory**: one-entry list gives `CronTrigger`; two entries give `OrTrigger` with two UTC children; Monday 08:00 and Friday 10:00 produce the expected next fire times from a fixed `now`; coinciding entries give one fire time; per-entry end dates (one expired) still fire on the other; all expired gives `None`; the trigger round-trips through pickle.
- **Service**: create and update with a list register one job under the schedule id; switching between `cron`, `crons` and `date` on update replaces the trigger; failed registration rolls back or restores as before.
- **Endpoint/integration**: POST/PUT/GET with `crons` return it as submitted; list endpoint reports `trigger_type: cron`; 422 locations; legacy row without `crons` reads back.

## Complexity Tracking

No constitution violations to justify.

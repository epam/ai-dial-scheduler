# Code Review: 041-multi-cron-triggers

- **spec-dir**: `specs/041-multi-cron-triggers/`
- **Date**: 2026-10-30
- **Mode**: Round 2 (Verification). Round 1 was a fresh review.
- **Artifacts consulted**: `spec.md`, `plan.md`, `research.md`, `contracts/trigger-crons.md`, `tasks.md`,
  `.specify/memory/constitution.md`, `CLAUDE.md`
- **Scope**: uncommitted diff on `041-multi-cron-triggers` (`src/lib/constants.py`, `src/lib/schemas/schedule.py`,
  `src/lib/services/trigger_factory.py`, `src/lib/services/schedule_service.py`, `src/api/v1/endpoints/schedules.py`,
  docs, CHANGELOG and the unit and integration tests)

## Quality gates run

| Gate | Round 1 | Round 2 |
|------|---------|---------|
| `uv run pytest tests/unit` | 1079 passed | 1086 passed |
| `uv run ruff check src tests` | clean | clean |
| `uv run ruff format --check src tests` | clean (178 files) | clean (178 files) |
| `uv run mypy src` | clean (85 files) | clean (85 files) |
| Integration tests | not run (no database) | not run (no database) |

## Summary

**Round 2: Approved with minor suggestions.** C1 and I1 are resolved and covered by tests, and M2 is resolved. M1 and
M3 are declined with reasons I accept. Two new minor, optional notes (N1, N2) were raised by the fixes. Nothing blocks
the merge.

Round 1 (kept for history): Requires changes. Per-entry `start_date` was not enforced under `OrTrigger` (C1), and
invalid field values returned a 502 with no entry index (I1).

## Status overview

| ID | Severity | Title | Status |
|----|----------|-------|--------|
| C1 | Critical | Per-entry `start_date` ignored after the combined trigger fires | Resolved |
| I1 | Improvement | Invalid field value returns 502 without entry index | Resolved |
| M1 | Minor | Integration tests lack `next_run_time` assertions | Declined |
| M2 | Minor | Invalid stored trigger in service-test fixtures | Resolved |
| M3 | Minor | `unittest.mock` instead of `pytest-mock` | Declined |
| N1 | Minor (new) | `EntryCronTrigger` import path is now part of the persisted job format | Open (optional) |
| N2 | Minor (new) | `research.md` R6 wording and section order | Open (optional) |

---

## Critical Issues

### C1: Per-entry `start_date` is ignored after the combined trigger has fired once
- **Status**: Resolved (round 2)
- **Where**: `src/lib/services/trigger_factory.py`
- **Original problem (round 1)**: `OrTrigger` passes the combined previous fire time to every child, and `CronTrigger`
  checks `start_date` only when `previous_fire_time is None`. An entry therefore fired before its own `start_date`
  once a sibling had fired: with Monday 08:00 plus Friday 10:00 from 2026-11-20, it fired on 2026-11-06. This broke
  FR-002 and the "each entry honours its own bounds" edge case.
- **Resolution**: `EntryCronTrigger(CronTrigger)` overrides `get_next_fire_time`. It drops the previous fire time
  (`None`) when `now < start_date`, and otherwise clamps it to `start_date - 1us`, so slots missed after the start are
  still caught up. `_build_crons_trigger` uses it only for `OrTrigger` children. The single `cron` path and the
  one-entry list still build a plain `CronTrigger`, so job-store rows for existing schedules are unchanged
  (FR-009/FR-010). `TestBuildTriggerCronsStartDate` covers the stepped fire sequence (11-02, 11-09, 11-16, Fri 11-20),
  the catch-up case and a pickle round trip. The coordinator confirmed these tests fail with plain children. Both
  datetimes in the comparison are timezone-aware: APScheduler normalises `start_date` in `__init__`, and the scheduler
  passes aware fire times. `research.md` R1 now describes the caveat correctly.

---

## Improvements

### I1: Invalid field value in an entry returns 502 with no entry index (US4.2, FR-008, SC-005)
- **Status**: Resolved (round 2)
- **Where**: `src/lib/schemas/schedule.py` (`CronTriggerSpec.trigger_kwargs`, `CronTriggerSpec.validate_fields_are_schedulable`)
- **Original problem (round 1)**: `CronFields` checked types only, so `hour: 25` passed validation. It then failed in
  `build_trigger` after the row was written and came back as a 502 with no entry index.
- **Resolution**:
  - A model validator now builds a throwaway `CronTrigger` from `trigger_kwargs()`. Its `ValueError` becomes a
    Pydantic 422 located at `trigger.crons.<i>` (or `trigger.cron`).
  - `trigger_kwargs()` is now the single source for `CronTrigger` arguments, used by both the validator and the
    factory, which keeps it DRY.
  - Tests cover schema, endpoint and integration level, for both `crons` index 1 and a single `cron`.
  - CHANGELOG has a `Changed` entry for the 502 to 422 change on single `cron`, and `research.md` R9 records the decision.
- **Verification**:
  - I probed odd inputs: `"abc"`, `"mon-xyz"`, `99999`, `"*/0"`, `-1`, `""`, `"1,,2"`, `"13"` and `"2026-2020"`.
    Every one raises `ValueError` and so becomes a 422. None escapes as an unhandled exception, which would be a 500.
    `"last"` is valid for `day` and is accepted.
  - `end_date < start_date` is still accepted, as before this feature. The schedule then simply never fires. This is
    out of scope and not counted as a finding.

---

## Minor Suggestions

### M1: Integration tests skip the `next_run_time` assertions that T007/T012 require
- **Status**: Declined (accepted)
- **Reason given**: the integration fixture mocks APScheduler, so `next_run_time` is always `null` there. Existing
  tests already document this, and fire-time behaviour is asserted through the registered trigger object instead. This
  is reasonable. Optionally, reword T007/T012 in `tasks.md` so they match what the tests actually verify.

### M2: Invalid stored trigger in service-test fixtures
- **Status**: Resolved (round 2)
- **Resolution**: the create tests in `TestScheduleServiceCrons` now store the real dumped request trigger instead of
  `{"crons": []}`.

### M3: Mocking style
- **Status**: Declined (accepted)
- **Reason given**: the new tests follow the existing file's `unittest.mock` style. That consistency is a valid reason
  to keep it.

### N1 (new): `EntryCronTrigger` import path is now part of the persisted job format
- **Status**: Open (optional)
- **Where**: `src/lib/services/trigger_factory.py`, class `EntryCronTrigger`
- **Note**: `OrTrigger.__getstate__` stores each child as `obj_to_ref(cls)`, which here is
  `src.lib.services.trigger_factory:EntryCronTrigger`. That string is written into `apscheduler_jobs` rows. Renaming or
  moving the class later would make the worker fail to load every multi-cron job. Add one line to the class docstring,
  for example "Referenced by import path in persisted APScheduler jobs; do not rename or move without a job migration",
  so the constraint is visible to future refactors. No code change is needed.

### N2 (new): `research.md` R6 wording and section order
- **Status**: Open (optional)
- **Where**: `specs/041-multi-cron-triggers/research.md`
- **Note**: R6 still says the bounds are "passed to its own `CronTrigger`" and does not mention `EntryCronTrigger`,
  though R1 now explains it. R9 also appears between R6 and R7. To fix, add a one-sentence pointer from R6 to R1 and
  move R9 after R8. This is documentation only.

---

## Intent Alignment

| Requirement | Round 2 status |
|-------------|----------------|
| FR-001, FR-003, FR-004, FR-005 | Met |
| FR-002 / per-entry bounds edge case | Met (C1 resolved; `start_date` and `end_date` both honoured) |
| FR-006, FR-012 (1 to 20 entries) | Met |
| FR-007 (exactly one kind) | Met |
| FR-008 / SC-005 (error names the entry) | Met for type and value errors (I1 resolved) |
| FR-009, FR-010 (backward compatibility) | Met. Single-cron and one-entry paths still build a plain `CronTrigger`, and stored rows without `crons` validate. The only deliberate change, invalid single `cron` returning 422 instead of 502, is recorded under CHANGELOG `Changed`. Previously stored rows passed the same `CronTrigger` construction at registration, so the new validator does not reject them on read. |
| FR-011, FR-013, FR-014, FR-015 | Met |

## Checklist Pass

- **Correctness**: pass (C1 and I1 resolved, each with regression tests).
- **Backward compatibility**: pass.
- **Type safety**: pass (`mypy src` clean; the `get_next_fire_time` override is fully annotated).
- **Style/conventions**: pass (ruff check and format clean, comments in English, nothing added to `__init__.py`).
- **Architecture**: pass. `schemas/` now imports APScheduler's `CronTrigger` for validation. That is acceptable
  inside `lib/`, and it keeps a single source of truth through `trigger_kwargs()`.
- **Observability**: pass. `trigger_kind` and `cron_entries` appear on registration logs.
- **Security**: pass. No secrets or field values in the new logs.
- **Database**: pass. No schema change and no migration.
- **Testing**: pass. Tests now cover per-entry `start_date`, catch-up, pickling, and value errors at every layer.

# Phase 1 Data Model: Add Ordering for Schedules/Runs List

No persistent schema changes. This feature adds **query-time value objects and enums** only — no
new tables, columns, or migrations. Ordering keys reuse existing fields.

## Existing fields used as sort keys (no change)

| Sort field | Backing column | Notes |
|-----------|----------------|-------|
| Schedule creation date | `schedules.created_at` (`BaseSQLModel.created_at`, aware UTC datetime) | Not unique → needs tiebreaker. |
| Schedule next run date | `apscheduler_jobs.next_run_time` (epoch float, outer-joined) | NULL for paused/never-scheduled ("inactive") → must sort **last**. |
| Schedule name | `schedules.display_name` (String) | Sort case-insensitively (`lower(display_name)`); not unique → needs tiebreaker. |
| Run creation date | `schedule_runs.created_at` (`BaseSQLModel.created_at`) | Not unique → needs tiebreaker. |
| Tiebreaker (both) | primary key `id` | `schedules.id: str`, `schedule_runs.id: uuid`. Appended to every ordering. |

## New enums — `src/lib/db/enums.py`

```
class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"

class ScheduleSortField(StrEnum):
    CREATED_AT = "created_at"
    NEXT_RUN_TIME = "next_run_time"
    NAME = "name"

class RunSortField(StrEnum):
    CREATED_AT = "created_at"
```

- **Validation**: Any value outside an enum's members is rejected by FastAPI/Pydantic with a 422
  before any query runs (FR-007, SC-005).

## New value objects — `src/lib/schemas/ordering.py` (Pydantic v2)

Typed contracts carried across the api→lib boundary (no dicts). Constructed by the API dependency,
consumed by the service and repository.

- **`ScheduleOrdering`**
  - `order_by: ScheduleSortField = ScheduleSortField.CREATED_AT`
  - `order_dir: SortDirection = SortDirection.DESC`
- **`RunOrdering`**
  - `order_by: RunSortField = RunSortField.CREATED_AT`
  - `order_dir: SortDirection = SortDirection.DESC`

Defaults reproduce the current hardcoded behavior (created_at desc), so the feature is additive.

## Ordering resolution rules (repository)

Applied as `.order_by(...)` between the existing `.where(*conditions)` and `.limit().offset()`; the
count query is untouched (FR-008).

### Schedules (`ScheduleRepository.list`)

| order_by | order_dir | Effective `.order_by(...)` |
|----------|-----------|----------------------------|
| `created_at` | `asc` | `created_at.asc(), id.asc()` |
| `created_at` | `desc` | `created_at.desc(), id.desc()` |
| `next_run_time` | `asc` | `next_run_time.asc().nulls_last(), id.asc()` |
| `next_run_time` | `desc` | `next_run_time.desc().nulls_last(), id.desc()` |
| `name` | `asc` | `func.lower(display_name).asc(), id.asc()` |
| `name` | `desc` | `func.lower(display_name).desc(), id.desc()` |

- `next_run_time` refers to `apscheduler_jobs.c.next_run_time`. `nulls_last()` guarantees inactive
  (NULL) schedules come after all active ones in **both** directions (FR-003, SC-002).
- `name` maps to `schedules.display_name`, ordered case-insensitively via `func.lower(...)` so names
  that differ only by case sort deterministically (FR-004; edge case in spec).

### Runs (`ScheduleRunRepository.list_for_schedule`)

| order_by | order_dir | Effective `.order_by(...)` |
|----------|-----------|----------------------------|
| `created_at` | `asc` | `created_at.asc(), id.asc()` |
| `created_at` | `desc` | `created_at.desc(), id.desc()` |

## State / relationships

- No entity relationships change. `Schedule` ↔ `apscheduler_jobs` remains an outer join on
  `schedules.id == apscheduler_jobs.id`. `ScheduleRun.schedule_id` FK unchanged.
- No state transitions; ordering is stateless and per-request (not persisted as a preference).

## Invariants (test targets)

1. For any field+direction, appending the PK makes the total order strict → stable pagination
   (FR-006, SC-004).
2. `next_run_time` ordering: every NULL-next-run schedule appears after every non-NULL one, for
   both `asc` and `desc` (FR-003, SC-002).
3. Default (no params) output is byte-for-byte the current `created_at desc, id desc` order (FR-005).
4. Filtering (`metadata`/`name`) and the total `count` are unaffected by ordering (FR-008).

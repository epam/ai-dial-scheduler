# Phase 1 Data Model: Schedule Run List & Run Details Endpoints

This feature adds **no new persistent entities and no migration**. It reads the existing
`schedule_runs` and `schedules` tables and introduces only API-facing Pydantic response schemas.

---

## Existing persistence (read-only inputs)

### `ScheduleRun` — `src/lib/models/schedule_run.py` (table `schedule_runs`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Run identifier; surfaced as `id`. |
| `created_at` | `datetime` (tz) | Inherited; fire time → surfaced as `start_time` (see research D1). |
| `end_time` | `datetime \| None` (tz) | NULL while in progress. |
| `status` | `str` | Constrained to `ScheduleRunStatus` values. |
| `result` | `dict \| None` (JSONB) | Produced result on success; `{"error_text": ...}` on error; NULL while in progress. |
| `schedule_id` | `str` | FK → `schedules.id` (indexed `IX_schedule_runs_schedule_id`). |
| `schedule_snapshot` | `dict` (JSONB) | Fire-time job kwargs (properties only). **Not** used for the run-details embed (see research D2). |
| `updated_at` | `datetime` (tz) | Inherited; not exposed. |

### `ScheduleRunStatus` — `src/lib/db/enums.py`

`success` · `error` · `in_progress` · `missed`

### `Schedule` — `src/lib/models/schedule.py` (table `schedules`)

Used for ownership (`created_by`) and the run-details embed: `display_name`,
`trigger` (JSONB), `kwargs` (JSONB → exposed as `properties`).

---

## New API schemas — `src/lib/schemas/run.py`

> `RunContext` (existing) stays unchanged.

### `RunListItem` — one row in the run list

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `id` | `uuid.UUID` | `ScheduleRun.id` | Serialized as string in JSON. |
| `status` | `ScheduleRunStatus` | `ScheduleRun.status` | Enum value (`success`/`error`/`in_progress`/`missed`). |
| `start_time` | `datetime` | `ScheduleRun.created_at` | Always present. |
| `end_time` | `datetime \| None` | `ScheduleRun.end_time` | `null` while in progress. |

### `RunScheduleSummary` — embedded schedule in run details

| Field | Type | Source |
|-------|------|--------|
| `display_name` | `str` | `Schedule.display_name` |
| `trigger` | `dict[str, Any]` | `Schedule.trigger` |
| `properties` | `dict[str, Any]` | `Schedule.kwargs` |

### `RunResponse` — full run detail

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `id` | `uuid.UUID` | `ScheduleRun.id` | |
| `status` | `ScheduleRunStatus` | `ScheduleRun.status` | |
| `start_time` | `datetime` | `ScheduleRun.created_at` | |
| `end_time` | `datetime \| None` | `ScheduleRun.end_time` | |
| `result` | `dict[str, Any] \| None` | `ScheduleRun.result` | Result on success, `{"error_text": ...}` on error, `null` in progress. |
| `schedule` | `RunScheduleSummary` | live `Schedule` row | See research D2. |

### `Page[RunListItem]` (existing generic — `src/lib/schemas/pagination.py`)

`count` · `limit` · `offset` · `results: list[RunListItem]` · `next: str \| None` ·
`previous: str \| None`.

---

## State & relationships

- A `ScheduleRun` belongs to exactly one `Schedule` via `schedule_id`. Runs are listed/fetched only
  through their owning schedule.
- These endpoints are **read-only**: they never transition run status. The lifecycle
  (`in_progress → success | error`, plus `missed`) is owned by feature 009.

---

## Validation rules (enforced in this feature)

| Rule | Where | Result |
|------|-------|--------|
| `schedule_id` must exist AND be owned by caller | `ScheduleRunService` via `ScheduleRepository.get(schedule_id, created_by)` | 404 `ScheduleNotFoundError` |
| `run_id` must be a well-formed UUID | FastAPI path param `run_id: uuid.UUID` | 422 |
| run must exist AND its `schedule_id` must equal the path `schedule_id` | `ScheduleRunRepository.get_for_schedule` | 404 `ScheduleRunNotFoundError` |
| `limit >= 1`, `offset >= 0` | `Pagination` dependency (`Query` constraints) | 422 |
| Offset beyond `count` | `Paginator.paginate` | 200 with empty `results`, `next = null` |

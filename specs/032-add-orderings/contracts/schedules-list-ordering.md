# Contract: Schedules List Ordering

**Endpoint**: `GET /v1/schedules`
**Handler**: `src/api/v1/endpoints/schedules.py::list_schedules`
**Change type**: Additive (non-breaking). Defaults preserve current behavior; no major version bump.

## New query parameters

| Param | Type (enum) | Allowed values | Default | Description |
|-------|-------------|----------------|---------|-------------|
| `order_by` | `ScheduleSortField` | `created_at`, `next_run_time`, `name` | `created_at` | Field to sort by. |
| `order_dir` | `SortDirection` | `asc`, `desc` | `desc` | Sort direction. |

Existing params (`limit`, `offset`, `name`, `metadata__*`) are unchanged and compose with ordering.

## Semantics

- `order_by=created_at`: sort by schedule creation timestamp; `id` breaks ties.
- `order_by=next_run_time`: sort by the schedule's next scheduled run time; schedules with **no**
  next run time (paused/inactive) always appear **after** all schedules that have one, in both
  `asc` and `desc`. `id` breaks ties.
- `order_by=name`: sort by the schedule's display name, **case-insensitively** (A→Z / Z→A). `id`
  breaks ties.
- Omitting both params yields the pre-existing order: `created_at desc` (newest first).

## Validation / errors

- An `order_by` or `order_dir` value outside the allowed set → **HTTP 422** (FastAPI validation),
  with no results returned (FR-007, SC-005).
- `order_by=next_run_time` is valid here (unlike the runs endpoint).

## Pagination interaction

- `next` / `previous` links in the `Page` response echo the active `order_by` and `order_dir`
  (in addition to `limit`/`offset`) so paginated traversal preserves the order (SC-004).

## Response

- Response body shape (`Page[ScheduleListItem]`) is unchanged. Only the order of `results` changes.

## Acceptance checks (maps to spec)

| Check | Spec ref |
|-------|----------|
| `order_by=created_at&order_dir=asc` → oldest→newest | US1 / FR-001 |
| `order_by=created_at&order_dir=desc` → newest→oldest | US1 / FR-001 |
| `order_by=next_run_time&order_dir=asc` → soonest→latest, inactive last | US2 / FR-002, FR-003 |
| `order_by=next_run_time&order_dir=desc` → latest→soonest, inactive last | US2 / FR-002, FR-003 |
| `order_by=name&order_dir=asc` → A→Z (case-insensitive) | US3 / FR-004 |
| `order_by=name&order_dir=desc` → Z→A (case-insensitive) | US3 / FR-004 |
| no params → `created_at desc` | FR-006 |
| ties resolved by `id` → stable across pages | FR-007, SC-005 |
| invalid value → 422 | FR-008, SC-006 |
| ordering + `name`/`metadata` filter → same matched rows, reordered | FR-009 |

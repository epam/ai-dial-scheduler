# Contract: Runs List Ordering

**Endpoint**: `GET /v1/schedules/{schedule_id}/runs`
**Handler**: `src/api/v1/endpoints/runs.py::list_runs`
**Change type**: Additive (non-breaking). Defaults preserve current behavior; no major version bump.

## New query parameters

| Param | Type (enum) | Allowed values | Default | Description |
|-------|-------------|----------------|---------|-------------|
| `order_by` | `RunSortField` | `created_at` | `created_at` | Field to sort by. |
| `order_dir` | `SortDirection` | `asc`, `desc` | `desc` | Sort direction. |

Existing params (`limit`, `offset`) are unchanged and compose with ordering.

## Semantics

- `order_by=created_at`: sort by run creation timestamp; `id` breaks ties.
- Omitting both params yields the pre-existing order: `created_at desc, id desc` (newest first).

## Validation / errors

- An `order_by` or `order_dir` value outside the allowed set → **HTTP 422**, no results returned
  (FR-007, SC-005). Note `next_run_time` is **not** a valid `order_by` for runs.
- Unowned/unknown `schedule_id` still returns **404** as today (ownership check precedes ordering).

## Pagination interaction

- `next` / `previous` links echo the active `order_by`/`order_dir` alongside `limit`/`offset`
  (SC-004).

## Response

- Response body shape (`Page[RunListItem]`) is unchanged. Only the order of `results` changes.

## Acceptance checks (maps to spec)

| Check | Spec ref |
|-------|----------|
| `order_by=created_at&order_dir=asc` → oldest→newest | US4 / FR-005 |
| `order_by=created_at&order_dir=desc` → newest→oldest | US4 / FR-005 |
| no params → `created_at desc` | FR-006 |
| ties resolved by `id` → stable across pages | FR-007, SC-005 |
| invalid value (incl. `next_run_time`, `name`) → 422 | FR-008, SC-006 |

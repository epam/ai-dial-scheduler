# Contract: Schedules List Deletion Filter

**Endpoint**: `GET /v1/schedules`
**Handler**: `src/api/v1/endpoints/schedules.py::list_schedules`
**Change type**: Additive (non-breaking). The default preserves the pre-feature result set, since no
schedule could be soft-deleted before this feature. No major version bump.

## New query parameter

| Param | Type | Allowed values | Default | Description |
|-------|------|----------------|---------|-------------|
| `is_deleted` | `boolean` | `true`, `false` (plus FastAPI's usual boolean spellings, e.g. `1`/`0`) | `false` | Selects live schedules (`false`) or soft-deleted schedules (`true`). |

Existing params (`limit`, `offset`, `name`, `metadata__*`, `order_by`, `order_dir`) are unchanged and
compose with this filter using AND.

## Semantics

- The filter is **two-valued**, not tri-valued: there is no "all" option, so a single response never
  mixes live and soft-deleted schedules (research.md D5).
- `is_deleted=false` (default) → only schedules with `is_deleted = false`.
- `is_deleted=true` → only schedules with `is_deleted = true`.
- Hard-deleted schedules appear under **neither** value — their rows no longer exist.
- The predicate is applied to the count query as well as the page query, so `count` reports the
  filtered total. A default-filtered response never counts soft-deleted schedules (FR-012).

## Pagination interaction

`next` / `previous` links echo `is_deleted=true` alongside `limit`, `offset`, `order_by`, and
`order_dir`, so traversing a deleted-only list cannot silently flip to live schedules on page 2. The
parameter is omitted from the links when it holds its default (`false`), keeping existing link shapes
byte-identical for the default view.

Implementation note: `ScheduleService.list` currently **assigns**
`paginator.extra_query = ordering.to_query()`; it must **append** the filter fragment instead of
overwriting. (`name` and `metadata__*` are not echoed today — a pre-existing gap that this feature
does not fix, but must not extend to `is_deleted`.)

## Validation / errors

- A non-boolean value (`?is_deleted=maybe`) → **HTTP 422** from FastAPI validation, with no results
  returned.
- Repeating the parameter follows FastAPI's standard single-value behaviour; no special handling.
- Ownership scoping is unchanged and applied first: another user's schedules are never returned under
  either value.

## Response

Body shape is `Page[ScheduleListItem]` as before, with the added `is_deleted` field on each item (see
`schedule-is-deleted-field.md`). Only which rows are returned — and the `count` — change.

## Acceptance checks (maps to spec)

| Check | Spec ref |
|-------|----------|
| No `is_deleted` param → only live schedules | US3 / FR-011 |
| `is_deleted=true` → only soft-deleted schedules | US3 / FR-010 |
| Neither value returns a hard-deleted schedule | US1 / FR-003, SC-003 |
| Every returned item exposes `is_deleted` | US3 / FR-008 |
| `count` reflects the deletion filter | US3 / FR-012 |
| Filter composes with `name`, `metadata__*`, `order_by`/`order_dir`, `limit`/`offset` | US3 / FR-012 |
| `next`/`previous` preserve `is_deleted=true` across pages | FR-012 |
| Invalid boolean value → 422 | FR-010 (validation) |
| Default list contains zero soft-deleted; deleted list contains zero live | SC-004 |

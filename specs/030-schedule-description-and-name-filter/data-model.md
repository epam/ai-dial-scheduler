# Phase 1 Data Model: Schedule Description & Name Filter

## Entity: Schedule (modified)

Existing entity (`src/lib/models/schedule.py`). This feature adds one attribute.

### New field

| Field | Type | Nullable | Default | Notes |
|-------|------|----------|---------|-------|
| `description` | `str \| None` (`VARCHAR`) | Yes | NULL (no server default) | Optional free-text notes. Absent ⇒ NULL. |

- No index required — `description` is neither filtered nor sorted. (The name filter targets the existing `display_name` column, not `description`.)
- Length is enforced at the API schema boundary (`max_length=1000`), not as the primary DB constraint. A DB-level `VARCHAR(1000)` MAY be added defensively but the Pydantic validator is authoritative.

### Existing fields (unchanged, referenced by this feature)

| Field | Role in this feature |
|-------|----------------------|
| `display_name` | Target of the case-insensitive substring **name filter**. |
| `created_by` | Owner scope — name filter only matches the requesting user's schedules. |
| `schedule_metadata` (`metadata` column) | Composes with the name filter via AND. |
| `trigger` (JSONB) | Now surfaced on **list rows** (`ScheduleListItem.trigger`), identical to the details shape. Already loaded on the ORM model — no new query. |

### Migration

- New Alembic revision `add_description_to_schedules`, `down_revision` = current head (`c3f1a2b4d5e6`).
- `upgrade()`: `op.add_column("schedules", sa.Column("description", sa.String(length=1000), nullable=True))`.
- `downgrade()`: `op.drop_column("schedules", "description")`.
- No data backfill — pre-existing rows get NULL, satisfying FR-015.

## Validation Rules

| Rule | Source | Enforcement point |
|------|--------|-------------------|
| Description length ≤ 1000 characters | FR-006, SC-002 | `ScheduleRequest.description` — Pydantic `Field(max_length=1000)` → 422 on violation |
| Description optional on create | FR-005 | `ScheduleRequest.description` defaults to `None` |
| Description replaced (not merged) on update | Assumptions | Service passes new value through; repository assigns it unconditionally |
| Whitespace-only description accepted | Edge case | No `strip`/non-empty validator applied to `description` (unlike `display_name`) |
| Name filter empty ⇒ no filter | FR-011, Edge case | `parse_name_filter` normalizes empty/whitespace to `None` |
| Name match case-insensitive substring | FR-010 | Repository `display_name.ilike("%" + escaped_term + "%")` |
| Owner scope preserved | FR-012 | Existing `created_by` WHERE condition retained |

## State & Relationships

- No state machine changes; description does not affect trigger/execution/pause/resume.
- No new relationships; `description` is a scalar attribute of `Schedule`.

## Field flow across layers

```text
ScheduleRequest.description (max_length=1000, default None)
  → ScheduleService.create/update
    → ScheduleRepository.create/update  → Schedule.description (DB)
      → ScheduleService._to_response / list mapping
        → ScheduleResponse.description  (details)
        → ScheduleListItem.description  (list rows)

Schedule.trigger (JSONB, already persisted)
  → ScheduleService.list mapping
    → ScheduleListItem.trigger  (list rows, identical to ScheduleResponse.trigger)

?name=<term> query param
  → parse_name_filter → str | None
    → ScheduleService.list(..., name_filter=...)
      → ScheduleRepository.list(..., name_filter=...)  → WHERE display_name ILIKE %term%
```

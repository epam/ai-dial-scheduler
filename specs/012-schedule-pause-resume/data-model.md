# Phase 1 Data Model: Schedule Pause & Resume Endpoints

This feature introduces **no new entities, columns, schemas, or migrations**. It reuses
existing structures and changes only the *runtime firing state* of an already-registered
APScheduler job. This document records the entities involved and the state transition.

## Entities (existing — unchanged)

### Schedule (`src/lib/models/schedule.py`)

The owning entity. Used here read-only for authorization and for the response body.

| Field | Role in this feature |
|-------|----------------------|
| `id` | Schedule identifier; **also the APScheduler job id** used to pause/resume. |
| `created_by` | Ownership key; the action is allowed only when it equals the caller's resolved identity. |
| `display_name`, `trigger`, `kwargs`, `created_at`, `updated_at` | Returned unchanged in `ScheduleResponse`. **Not modified** by pause/resume. |

### APScheduler Job row (`apscheduler_jobs`, shared job store)

Not an ORM model owned by this app; read via `ScheduleRepository`'s outer join for
`next_run_time`. Pause/resume mutate **only** this row:

| Field | Pause | Resume |
|-------|-------|--------|
| `next_run_time` | set to `NULL` (job will not fire) | recomputed from the trigger; row removed if the trigger has no future fire time |

### ScheduleResponse (`src/lib/schemas/schedule.py` — existing Pydantic v2 schema)

Reused verbatim as the response body. After a pause, `next_run_time` is `null`; after a
resume it reflects the next firing (or `null` when the trigger is exhausted).

## State transition

```text
            POST /schedules/{id}/pause
   ACTIVE  ───────────────────────────▶  PAUSED
 (next_run_time set)                  (next_run_time = NULL)
        ▲                                   │
        └───────────────────────────────────┘
            POST /schedules/{id}/resume
       (next_run_time recomputed from trigger;
        job removed if no future fire time)
```

- **Idempotent**: `pause` on `PAUSED` stays `PAUSED`; `resume` on `ACTIVE` stays `ACTIVE`.
- **Not-found edge**: if the owned schedule has no job row (e.g. an exhausted one-off), the
  transition is undefined and the request returns **404** (see research.md §4).

## Validation & authorization rules (from requirements)

- **Authentication** (FR-008): caller identity resolved by the existing `get_user_id`
  dependency; unresolved identity → 401, before any state change.
- **Ownership** (FR-003, FR-004): `ScheduleRepository.get(schedule_id, created_by)` returns
  `None` for missing *or* non-owned schedules → `ScheduleNotFoundError` → 404 (no existence
  disclosure).
- **No persistence change** (FR-005): the `schedules` row is read, never written.

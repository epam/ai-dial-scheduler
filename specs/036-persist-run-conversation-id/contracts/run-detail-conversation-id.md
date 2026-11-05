# Contract: Get Schedule Run Detail — `conversation_id` field

**Method**: `GET`
**Path**: `/v1/schedules/{schedule_id}/runs/{run_id}`
**Auth**: DIAL auth headers (resolved to a user id via `get_user_id`); the schedule must be owned by
the caller.
**Change type**: Additive, nullable response field. Non-breaking — no major version bump.
**Schema**: `src/lib/schemas/run.py` — `RunResponse`
**Requirement**: research.md D4 (backward-compatible extension the spec permits; consistency with the
mandated list surface)

## Request

Unchanged.

| Kind | Name | Type | Notes |
|------|------|------|-------|
| Path | `schedule_id` | string | Owned schedule id |
| Path | `run_id` | uuid | Run under that schedule |

No request body.

## Response

**Success `200`** — `RunResponse`, now including `conversation_id`:

```json
{
  "id": "8f0d...-uuid",
  "status": "success",
  "start_time": "2026-08-26T10:00:00Z",
  "end_time": "2026-08-26T10:00:03Z",
  "result": { "result": { "choices": [ ... ] } },
  "conversation_id": "conversations/<bucket>/.scheduler/<schedule_id>/<deployment>__<name>__<run-id>",
  "schedule": {
    "display_name": "Daily digest",
    "trigger": { "...": "..." },
    "properties": { "...": "..." }
  }
}
```

| Field | Type | Nullable | Meaning |
|-------|------|----------|---------|
| `conversation_id` | `string` | yes (`null`) | Same addressable DIAL conversation id as the matching list entry, `null` when none |

**Error cases** (unchanged):

| Condition | Status | Body |
|-----------|--------|------|
| Schedule not found or not owned | `404` | `{"detail": "Schedule not found: {schedule_id}"}` |
| Run not found under the schedule | `404` | `{"detail": "Run not found: {run_id}"}` |

## Behaviour

`conversation_id` is read verbatim from `schedule_runs.conversation_id`
(`ScheduleRunService.get_run` maps `conversation_id=run.conversation_id`). For any given run it is
**identical** to the value returned by the list endpoint for that run (both read the same column),
so a consumer switching between the two views sees a consistent id (SC-005). Absence rules are
identical to the list contract (FR-005).

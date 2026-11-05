# Contract: List Schedule Runs — `conversation_id` field

**Method**: `GET`
**Path**: `/v1/schedules/{schedule_id}/runs`
**Auth**: DIAL auth headers (resolved to a user id via `get_user_id`); the schedule must be owned by
the caller.
**Change type**: Additive, nullable response field on each list item. Non-breaking — no major version
bump.
**Schema**: `src/lib/schemas/run.py` — `RunListItem`
**Requirement**: FR-004 (mandated surface)

## Request

Unchanged.

| Kind | Name | Type | Notes |
|------|------|------|-------|
| Path | `schedule_id` | string | Owned schedule id |
| Query | `limit`, `offset` | int | Existing pagination |
| Query | `order_by`, `order_dir` | enum | Existing ordering (`created_at`, `asc`/`desc`) |

No request body.

## Response

**Success `200`** — paginated `Page[RunListItem]`; each item gains `conversation_id`:

```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "8f0d...-uuid",
      "status": "success",
      "start_time": "2026-08-26T10:00:00Z",
      "end_time": "2026-08-26T10:00:03Z",
      "conversation_id": "conversations/<bucket>/.scheduler/<schedule_id>/<deployment>__<name>__<run-id>"
    },
    {
      "id": "1a2b...-uuid",
      "status": "error",
      "start_time": "2026-08-26T09:00:00Z",
      "end_time": "2026-08-26T09:00:01Z",
      "conversation_id": null
    }
  ]
}
```

| Field | Type | Nullable | Meaning |
|-------|------|----------|---------|
| `conversation_id` | `string` | yes (`null`) | Addressable DIAL conversation id for this run, `null` when no conversation was created |

**Error cases** (unchanged):

| Condition | Status | Body |
|-----------|--------|------|
| Schedule not found or not owned | `404` | `{"detail": "Schedule not found: {schedule_id}"}` |
| Invalid pagination/ordering params | `422` | FastAPI validation error |

## Behaviour

For each run in the page, `conversation_id` is read verbatim from the persisted
`schedule_runs.conversation_id` column (`ScheduleRunService.list_runs` maps
`conversation_id=run.conversation_id`).

- A run that successfully created a DIAL conversation carries the full addressable id
  (`conversations/...`), sufficient to locate and open that exact conversation without searching by
  name (FR-002, SC-003).
- A run reports `null` when: conversation creation was disabled on the schedule, the run did not
  complete successfully, the run is still in progress, conversation creation or its persistence did
  not complete, or the run predates this feature (FR-005, SC-002).
- The value is stable across repeated listings — once persisted it is never rewritten (FR-007,
  SC-005).
- Each run's `conversation_id` is independent; re-runs of the same schedule each carry their own id
  (the id embeds the `run_id`) (FR-008).
- Persisting or failing to persist the id never changes a run's `status`/`result` (FR-006, SC-004).

# Contract: Run Details

`GET /v1/schedules/{schedule_id}/runs/{run_id}`

Returns the full detail of a single run, including an embedded summary of its owning schedule.
Read-only.

## Path parameters

| Name | Type | Notes |
|------|------|-------|
| `schedule_id` | string | Schedule identifier (string UUID). |
| `run_id` | UUID | Run identifier. Malformed UUID → 422 (FastAPI validation). |

## Authorization

The schedule must exist and be owned by the caller, and the run must belong to that schedule.
Any failure of these conditions is reported as 404 with a generic detail (no existence disclosure).

## Responses

### 200 OK

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "error",
  "start_time": "2026-06-11T10:00:00Z",
  "end_time": "2026-06-11T10:00:01Z",
  "result": { "error_text": "Connection refused" },
  "schedule": {
    "display_name": "Nightly sync",
    "trigger": {},
    "properties": {}
  }
}
```

- `status` ∈ `success | error | in_progress | missed`.
- `result`: produced result on success; `{"error_text": ...}` on error; `null` while in progress.
- `end_time` is `null` while in progress.
- `schedule` reflects the schedule's current definition (`display_name`, `trigger`, `properties`).

### 404 Not Found

Schedule missing/not owned, **or** run missing, **or** run belongs to a different schedule.

```json
{ "detail": "Run not found: {run_id}" }
```

(Schedule-level ownership failure uses `{ "detail": "Schedule not found: {schedule_id}" }`.)

### 422 Unprocessable Entity

`run_id` is not a well-formed UUID. Standard FastAPI validation body.

## Maps to

FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-011.

# Contract: List Schedule Runs

`GET /v1/schedules/{schedule_id}/runs`

Returns a paginated, owner-scoped list of runs for a single schedule. Read-only.

## Path parameters

| Name | Type | Notes |
|------|------|-------|
| `schedule_id` | string | Schedule identifier (string UUID). |

## Query parameters

| Name | Type | Default | Constraint |
|------|------|---------|------------|
| `limit` | int | `20` | `>= 1` |
| `offset` | int | `0` | `>= 0` |

## Authorization

Results restricted to schedules created by the caller (`created_by`, currently `MOCK_USER_ID`).
A schedule that does not exist or is owned by another user is reported identically (404, generic
detail) — no existence disclosure.

## Responses

### 200 OK

Ordered by start time descending (`created_at DESC, id DESC`) for stable pagination.

```json
{
  "count": 242,
  "limit": 20,
  "offset": 40,
  "results": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "status": "success",
      "start_time": "2026-06-11T10:00:00Z",
      "end_time": "2026-06-11T10:00:03Z"
    }
  ],
  "next": "/schedules/{schedule_id}/runs?limit=20&offset=60",
  "previous": "/schedules/{schedule_id}/runs?limit=20&offset=20"
}
```

- `status` ∈ `success | error | in_progress | missed`.
- `end_time` is `null` for runs that have not finished.
- `next` / `previous` are `null` at the respective ends.
- A schedule that exists and is owned but has never run → `count: 0`, `results: []`, `next: null`.

### 404 Not Found

Schedule missing or not owned by the caller.

```json
{ "detail": "Schedule not found: {schedule_id}" }
```

### 422 Unprocessable Entity

Invalid `limit`/`offset` (violates `>= 1` / `>= 0`). Standard FastAPI validation body.

## Maps to

FR-001, FR-002, FR-003, FR-007, FR-008, FR-010, FR-011, FR-013.

# API Contract: Schedule Pause & Resume Endpoints (v1)

Extends the schedule management surface defined in
`specs/007-schedule-endpoints/contracts/schedule-endpoints.md`. These two routes were
previously listed there as "out of scope" and are now implemented. FastAPI generates the live
OpenAPI document; this file is the human-readable contract.

Conventions:
- Base path: `/schedules` (mounted on the v1 API surface). `redirect_slashes=False`.
- Identity is resolved per request via the DIAL-backed `get_user_id` dependency (the
  `Api-Key` header). All routes are ownership-scoped to `created_by`.
- Timestamps are ISO-8601 with timezone (UTC).
- The schedule `id` in the path is also the APScheduler job id.
- No request body. No persisted schedule field is modified; only the job's `next_run_time`
  changes in the shared APScheduler job store.

---

## 1. Pause Schedule

`POST /schedules/{schedule_id}/pause` → **200 OK**

**Behavior**: resolve caller identity; load the owned schedule via
`ScheduleRepository.get(schedule_id, created_by)` (else **404**); call
`scheduler.pause_job(schedule_id)` off the event loop (`asyncio.to_thread`). A
`JobLookupError` (owned schedule whose job no longer exists, e.g. an exhausted one-off) maps
to **404**. Re-read the schedule and return it; `next_run_time` is now `null`. Idempotent:
pausing an already-paused schedule returns 200.

**Response 200**: identical object shape to Create/Update `ScheduleResponse`.

```json
{
  "id": "5f1c…",
  "display_name": "Daily report",
  "next_run_time": null,
  "trigger": { "...": "stored trigger" },
  "properties": { "...": "stored properties" },
  "created_by": "user@example.com",
  "created_at": "2026-06-11T12:00:00+00:00",
  "updated_at": "2026-06-11T12:00:00+00:00"
}
```

| Status | When |
|--------|------|
| 200 | schedule paused (or already paused) |
| 401 | caller identity could not be resolved (missing/invalid credential) |
| 404 | schedule id missing, not owned by caller, or no live job exists for it |
| 502 | DIAL service unavailable during identity resolution (inherited from `get_user_id`) |

---

## 2. Resume Schedule

`POST /schedules/{schedule_id}/resume` → **200 OK**

**Behavior**: resolve caller identity; load the owned schedule via
`ScheduleRepository.get(schedule_id, created_by)` (else **404**); call
`scheduler.resume_job(schedule_id)` off the event loop. A `JobLookupError` maps to **404**.
Re-read the schedule and return it; `next_run_time` reflects the next firing derived from the
trigger, or `null` when the trigger yields no future firings (APScheduler removes the job).
Idempotent: resuming an already-active schedule returns 200.

**Response 200**: identical object shape to Create/Update `ScheduleResponse`.

```json
{
  "id": "5f1c…",
  "display_name": "Daily report",
  "next_run_time": "2026-06-13T09:00:00+00:00",
  "trigger": { "...": "stored trigger" },
  "properties": { "...": "stored properties" },
  "created_by": "user@example.com",
  "created_at": "2026-06-11T12:00:00+00:00",
  "updated_at": "2026-06-11T12:00:00+00:00"
}
```

| Status | When |
|--------|------|
| 200 | schedule resumed (or already active); `next_run_time` may be `null` if trigger exhausted |
| 401 | caller identity could not be resolved (missing/invalid credential) |
| 404 | schedule id missing, not owned by caller, or no live job exists for it |
| 502 | DIAL service unavailable during identity resolution (inherited from `get_user_id`) |

---

## Notes

- **No existence disclosure**: the 404 for a non-owned schedule is identical to the 404 for a
  non-existent one.
- **No body / no validation errors**: neither endpoint accepts a body, so 422 does not apply
  beyond malformed path handling.
- **Cross-process effect**: because the API and worker share the APScheduler
  `SQLAlchemyJobStore`, a pause/resume issued on the API is observed by the worker.

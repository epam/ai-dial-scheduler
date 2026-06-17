# Contract: ScheduleProperties with Timeout & Retry

This feature makes **additive, non-breaking** changes to the schedule create/update request contract and the schedule response contract. The affected object is `properties` (the `ScheduleProperties` model) within `ScheduleRequest` and `ScheduleResponse`. No new endpoints; no changed routes.

Affected endpoints (request `properties` / response `properties`):
- `POST /v1/schedules` (create)
- `PUT /v1/schedules/{schedule_id}` (update)
- `GET /v1/schedules/{schedule_id}` (detail)

Contract version impact: **MINOR** (additive optional fields). No major bump.

## New: RetryPolicy

```jsonc
// RetryPolicy
{
  "backoff_interval": [2, 5, 8],   // list[int], non-empty, each > 0 (seconds). Length = number of retries.
  "retryable_errors": [500, 502]   // list[int], non-empty, each in [100, 599] (HTTP status codes)
}
```

## Updated: ScheduleProperties (additive fields only)

```jsonc
{
  "target_type": "rest",                 // unchanged
  "method": "POST",                      // unchanged
  "url": "https://api.example.com/hook", // unchanged
  "payload": { },                        // unchanged
  "extra_headers": { },                  // unchanged
  "api_version": null,                   // unchanged

  // NEW — both optional / nullable:
  "timeout": 30,                         // int | null, > 0 (seconds). null/absent → default app timeout.
  "retry": {                             // RetryPolicy | null. null/absent → no retry (single attempt).
    "backoff_interval": [2, 5, 8],
    "retryable_errors": [500, 502]
  }
}
```

### Field rules

| Field | Type | Optional | Constraint | Default behaviour when omitted |
|-------|------|----------|------------|--------------------------------|
| `timeout` | integer | yes | `> 0` | Use `WORKER_HTTP_TIMEOUT_SECONDS` (default 60s) |
| `retry` | RetryPolicy | yes | see RetryPolicy | No retries; single attempt |
| `retry.backoff_interval` | array[int] | required if `retry` present | non-empty, each `> 0` | — |
| `retry.retryable_errors` | array[int] | required if `retry` present | non-empty, each `100..599` | — |

### Validation error responses (FR-013)

Invalid input is rejected at create/update with a 422 validation error (FastAPI/Pydantic standard). Examples that MUST be rejected:
- `timeout: 0` or `timeout: -5`
- `retry.backoff_interval: []`
- `retry.backoff_interval: [2, 0]` (non-positive element)
- `retry.retryable_errors: []`
- `retry.retryable_errors: [999]` (out of HTTP status range)

### Response contract

`ScheduleResponse.properties` echoes the stored properties verbatim, so `timeout` and `retry` (or their absence) appear exactly as supplied (FR-014). No change to `ScheduleResponse` field set is required — `properties` is already a free-form object sourced from `schedules.kwargs`.

## Backward compatibility

- Existing clients that omit `timeout`/`retry` are unaffected (defaults preserve current behaviour).
- Existing stored schedules (no `timeout`/`retry` in `kwargs`) deserialize with both fields `null`.
- The run-detail response (`GET /v1/schedules/{id}/runs/{run_id}`) is **unchanged**: `result` still holds only the final outcome (success payload or `error_text`). Per-attempt detail is emitted to logs only and is not exposed in the run API.

## Internal worker contract (API ↔ worker)

The worker continues to receive the full `properties` object as APScheduler job `kwargs` and reconstruct it via `ScheduleProperties(**kwargs)`. Because `timeout`/`retry` are part of `ScheduleProperties`, the typed internal contract between API and worker (Constitution IV) is preserved — no ad-hoc dict keys are introduced outside the Pydantic model.

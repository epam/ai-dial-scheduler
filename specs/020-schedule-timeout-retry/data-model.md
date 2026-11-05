# Phase 1 Data Model: Schedule Timeout & Retry

No database tables, columns, indexes, or constraints change. The new data lives in **existing JSONB columns**. This document describes the logical shapes and validation rules.

## 1. RetryPolicy (new Pydantic model)

Defined in `src/lib/schemas/schedule.py`. Nested inside `ScheduleProperties.retry`.

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| `backoff_interval` | `list[int]` | yes (if `retry` present) | non-empty; every element `> 0`. Order is significant — element *i* is the wait (seconds) before retry *i*. Length = number of retries. |
| `retryable_errors` | `list[int]` | yes (if `retry` present) | non-empty; every element in `[100, 599]` (valid HTTP status code range). |

Semantics:
- Total attempts for a firing = `1 + len(backoff_interval)`.
- A failed attempt is retried only if its HTTP `status_code` is in `retryable_errors` **and** retries remain.

## 2. ScheduleProperties (extended)

Existing model in `src/lib/schemas/schedule.py`, two fields added:

| Field | Type | Required | Default | Validation |
|-------|------|----------|---------|------------|
| `timeout` | `int \| None` | no | `None` | if provided, `> 0`. Integer seconds. `None` → use `worker_http.timeout_seconds`. |
| `retry` | `RetryPolicy \| None` | no | `None` | if provided, validated per RetryPolicy. `None` → single attempt, no retry. |

Existing fields (`target_type`, `method`, `url`, `payload`, `extra_headers`, `api_version`) are unchanged. Existing `validate_target_rules` and `validate_url` validators are unchanged; new field validators are added independently.

**Serialization path** (unchanged mechanism): `request.properties.model_dump(mode="json")` → stored in `Schedule.kwargs` (JSONB) → copied to `ScheduleRun.schedule_snapshot` at run time → reconstructed in the worker as `ScheduleProperties(**kwargs)`. Because `timeout`/`retry` are part of this model, no other serialization code changes.

### Example (stored `kwargs` / request `properties`)

```json
{
  "target_type": "rest",
  "method": "POST",
  "url": "https://api.example.com/hook",
  "payload": {"x": 1},
  "extra_headers": {},
  "api_version": null,
  "timeout": 30,
  "retry": {
    "backoff_interval": [2, 5, 8],
    "retryable_errors": [500, 502]
  }
}
```

## 3. ScheduleRun — final outcome only (DB unchanged; no attempt history persisted)

The `schedule_runs` table and the shape of its `result` JSONB are **completely unchanged** by this feature. Per-attempt detail is **not** written to the database — it is emitted to logs only (see §6). The run row stores the final outcome exactly as today:

- On success: the executor payload (e.g., `{"status_code": 200, "body": {...}}` for REST, `{"events": [...]}` for SSE, `{"result": ...}`/`{"chunks": [...]}` for chat_completion).
- On failure: `{"error_text": "..."}`.

`status` still records the final outcome (`success` / `error`) and `end_time` still marks completion. Retries happen within a single `in_progress` lifetime; only the final outcome flips the status.

### Per-attempt detail (logs only) — see §6

Each attempt is recorded in structured logs with: attempt number, `is_retry`, applied timeout, status code (when known), the backoff scheduled before a retry, and a terminal-reason event. Operators reconstruct the attempt history of a run by filtering logs on `run_id`/`schedule_id`. The orchestrator returns an in-memory `attempt_count` used only for the run-summary log line — it is never persisted.

## 4. ScheduleRunError (enriched, not persisted directly)

`src/lib/exceptions/errors.py`. Add an optional `status_code` so the orchestrator can match against `retryable_errors`.

| Attribute | Type | Notes |
|-----------|------|-------|
| `status_code` | `int \| None` | Populated by executors from HTTP/SDK status errors; `None` for transport/timeout failures. Existing `schedule_id`, `run_id` unchanged. |

## 5. Settings (read-only reference)

`worker_http.timeout_seconds` (default `60.0`, env `WORKER_HTTP_TIMEOUT_SECONDS`) is the "default application timeout" used when `properties.timeout` is `None`. No settings change required.

## State / flow notes

- Backward compatibility: schedules created before this feature have no `timeout`/`retry` keys in `kwargs`; `ScheduleProperties(**kwargs)` defaults both to `None` → default timeout, single attempt. The run `result` shape is unchanged, so existing run-detail consumers are unaffected.
- Run status transitions are unchanged: `in_progress` → (`success` | `error`). Retries happen entirely within a single `in_progress` lifetime; only the final outcome flips the status.

# Phase 0 Research: Schedule Timeout & Retry

All technical-context items are known from the existing codebase; the work below resolves the *design* questions the spec left open and records the rationale for each decision. No `NEEDS CLARIFICATION` markers remain.

## R1. Where do `timeout` and `retry` belong in the schema?

- **Decision**: Add them to `ScheduleProperties` (`src/lib/schemas/schedule.py`), not as new top-level fields on `ScheduleRequest`.
- **Rationale**: `ScheduleProperties` is already the per-execution call configuration (`url`, `method`, `payload`, …). It is serialized via `model_dump(mode="json")` into `schedules.kwargs` (JSONB), copied into each run's `schedule_snapshot`, returned in `ScheduleResponse.properties`, and reconstructed in the worker as `ScheduleProperties(**kwargs)`. Placing the new fields here means they flow end-to-end with **zero changes** to the API endpoint, `ScheduleService`, the repository write path, or `ScheduleResponse`.
- **Alternatives considered**:
  - *Top-level `ScheduleRequest.timeout` / `retry`*: would require new columns or a separate JSONB blob, plumbing through `ScheduleService._register_job`, the response mapper, and the worker `kwargs` contract. More code, more surface, no benefit. Rejected.

## R2. Do we need a database migration?

- **Decision**: No migration.
- **Rationale**: `timeout`/`retry` persist inside the existing `schedules.kwargs` JSONB. The run `result` shape is unchanged (per-attempt detail is logs-only — see R5). No new columns, indexes, or constraints. The constitution requires a migration only for a *schema change*; there is none.
- **Alternatives considered**: Dedicated `timeout`/`retry` columns for queryability — no requirement to query/filter by them (unlike metadata in feature 019). Rejected (YAGNI).

## R3. How is per-attempt timeout enforced across REST / SSE / chat_completion?

- **Decision**: Two complementary layers:
  1. The retry/timeout orchestrator wraps each attempt in `asyncio.wait_for(executor.execute(...), timeout=effective_timeout)` — the authoritative total wall-clock cap, uniform across all target types.
  2. Executors also pass `effective_timeout` to the underlying call (`httpx` per-request `timeout=`, OpenAI `.create(timeout=)`), so a schedule timeout larger than the shared client default is honoured and not pre-empted.
- **`effective_timeout`** = `properties.timeout` if set, else `worker_http.timeout_seconds` (the "default application timeout").
- **Rationale**: httpx read timeouts are per-read, not total — an SSE stream could exceed the intended cap. `asyncio.wait_for` guarantees the attempt is abandoned at the limit regardless of target type. Passing the value to the HTTP layer keeps connection behaviour consistent and prevents the shared 60s client default from cutting off legitimately longer schedule timeouts.
- **Alternatives considered**:
  - *Only set httpx per-request timeout*: does not bound total SSE duration; inconsistent semantics across target types. Rejected.
  - *Only `asyncio.wait_for`*: leaves the shared client's 60s default able to pre-empt longer schedule timeouts. Rejected as sole mechanism.
- **Outcome of a timeout**: `asyncio.TimeoutError` → recorded as a terminal failure for the run; **not** retried (no status code to match `retryable_errors`; consistent with spec Assumptions).

## R4. How are retryable errors detected?

- **Decision**: Retry only when a failed attempt raised `ScheduleRunError` carrying a `status_code` present in `retry.retryable_errors`. Enrich `ScheduleRunError` with an optional `status_code: int | None`.
- **Rationale**: Executors currently raise `ScheduleRunError(str(exc))`, losing the HTTP status. `retryable_errors` are HTTP status codes (e.g., 500, 502). Executors will extract the code:
  - REST/SSE: from `httpx.HTTPStatusError.response.status_code`.
  - chat_completion: from `openai.APIStatusError.status_code` (the OpenAI SDK raises typed status errors).
  - `httpx.TransportError` / timeouts: no status code → terminal, not retryable.
- **Alternatives considered**: Parsing status codes out of the stringified error message — fragile. Rejected.

## R5. How is the attempt history recorded? (logs only — explicit product decision)

- **Decision**: Per-attempt detail is recorded in **structured logs only**; it is **not** written to the database. The `ScheduleRun` row is unchanged and stores only the final outcome (success payload or `error_text`), as today. The orchestrator returns an in-memory `attempt_count` used solely for the run-summary log line.
- **Rationale**: The product owner directed that attempts must not be written to the DB and that logs are the sole record of attempts. This keeps the run `result` shape unchanged (zero disruption to run-list/run-detail endpoints) and requires no migration. The full attempt history is recoverable by filtering logs on `run_id`/`schedule_id`.
- **Constitution note**: Principle II says "all task runs ... and all attempts" should be persisted. This is a deliberate, owner-approved deviation for the *per-attempt* breakdown only — the run itself (with its final outcome) is still persisted. Recorded here and in plan.md's Constitution Check.
- **Alternatives considered**:
  - *Persist an `attempts[]` array in `result` JSONB* (the original design): rejected per the explicit decision to keep attempts out of the DB.
  - *One `ScheduleRun` row per attempt*: needs a grouping key + migration and changes run-endpoint semantics. Rejected.

## R6. Backoff and attempt-count semantics

- **Decision**: `backoff_interval` is an ordered list of integer seconds. The number of retries equals `len(backoff_interval)`. Total attempts = `1 + len(backoff_interval)`. Before retry *i* (0-indexed), `await asyncio.sleep(backoff_interval[i])`. Retrying stops immediately on success.
- **Rationale**: Matches the user's explicit definition ("backoff_interval defines both the intervals and attempts number"). `asyncio.sleep` keeps the event loop unblocked.

## R7. Validation rules

- **Decision** (Pydantic v2 validators in `ScheduleProperties` / new `RetryPolicy`):
  - `timeout`: optional positive integer (`> 0`).
  - `retry.backoff_interval`: non-empty list of positive integers (`> 0`).
  - `retry.retryable_errors`: non-empty list of integers in the valid HTTP status range (100–599). (Empty `retryable_errors` would make retry a no-op; reject it as a likely misconfiguration — clearer than silently never retrying.)
  - Both `timeout` and `retry` independently optional.
- **Rationale**: Fail fast at create/update with clear messages (FR-013). Range-checking `retryable_errors` prevents meaningless codes.
- **Note**: This tightens the "empty `retryable_errors` allowed" edge case from the spec toward rejection. Captured as an open confirmation item; default chosen is reject-empty for clarity. If the team prefers allowing empty (no-op), relax the validator — low-cost change.

## R8. Logging (Observability principle + FR-010..012)

- **Decision**: Structured `logger` events via `src.lib.utils.logging` with `schedule_id`/`run_id` already bound to contextvars in `jobs.py`. Per attempt include: `attempt` (number), `max_attempts`, `is_retry`, `timeout` applied, and `retryable_errors` when a retry policy is active.
  - Attempt start: e.g. `attempt_start` with `attempt`, `is_retry`.
  - Before a retry wait: `retry_scheduled` with `next_attempt`, `backoff_seconds`, and the triggering `status_code`.
  - Final failure after exhausting retries: `last_retry_failed` (explicitly states it was the last retry and that it failed).
  - Non-retryable failure: `attempt_failed_non_retryable` with `status_code`.
- **Rationale**: Directly satisfies FR-010/011/012 and SC-005; reuses existing structlog setup; no `print()`.

## Resolved unknowns summary

| Question | Resolution |
|----------|------------|
| Schema location of new fields | On `ScheduleProperties` |
| Migration needed? | No |
| Timeout enforcement | `asyncio.wait_for` per attempt + per-request HTTP timeout; default = `worker_http.timeout_seconds` |
| Retry trigger | `ScheduleRunError.status_code` ∈ `retryable_errors` |
| Attempt history | Logs only (not persisted); run row keeps final outcome unchanged |
| Backoff/count | retries = `len(backoff_interval)`; sleep each interval in order |
| Empty `retryable_errors` | Rejected at validation (confirmable) |
| Logging | Structured per-attempt events; explicit last-retry-failed event |

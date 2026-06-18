# Quickstart & Validation: Schedule Timeout & Retry

Validation guide proving the feature works end-to-end. Implementation details live in `tasks.md` (after `/speckit-tasks`) and the code; this file is the run/verify guide.

## Prerequisites

- `uv sync` completed; PostgreSQL reachable per `.env`.
- API and worker runnable as today (no new env vars; `WORKER_HTTP_TIMEOUT_SECONDS` already exists and is the default timeout).
- No migration required — confirm `uv run alembic upgrade head` reports nothing new for this feature.

## Automated checks (must pass)

```bash
uv run pytest                       # unit + integration + contract
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

Feature-focused tests to expect:
- `tests/lib/schemas/test_schedule.py` — accepts valid `timeout`/`retry`; rejects non-positive timeout, empty/zero `backoff_interval`, empty/out-of-range `retryable_errors`.
- `tests/worker/test_retry.py` — retries exactly `len(backoff_interval)` times on a retryable status; stops on success; no retry on non-retryable status; timeout abandons the attempt and is terminal; backoff waits applied in order (with patched sleep).
- `tests/worker/test_jobs.py` — orchestration wiring; success-after-retry and exhausted-retry persist only the final outcome (no `attempts` in `result`), and emit the expected per-attempt logs.
- Executor tests — `ScheduleRunError.status_code` populated from HTTP/SDK errors; per-attempt timeout passed through.

## Manual scenario walkthrough

Assume an authenticated client and the create endpoint `POST /v1/schedules`.

### Scenario A — Custom timeout (US1)

1. Create a schedule whose `properties.timeout` = `5` pointing at a deliberately slow target (responds in ~30s). Use a `date` trigger a few seconds out (or trigger immediately).
2. **Expected**: the run finishes ~5s after firing with `status = "error"` and `result.error_text` set; logs show `attempt_timed_out` with the applied `timeout=5`. No retry (timeout is terminal). No per-attempt data is written to the run row.

### Scenario B — No timeout set uses default (US1)

1. Create a schedule with no `timeout`.
2. **Expected**: execution uses `WORKER_HTTP_TIMEOUT_SECONDS` (60s default). Logs reflect the default timeout value.

### Scenario C — Retry then success (US2)

1. Point a schedule at a target that returns `500` once then `200`. Set `properties.retry = {"backoff_interval": [2, 5], "retryable_errors": [500]}`.
2. **Expected**: attempt 1 fails (500) → log `retry_scheduled` with `backoff_seconds=2` → wait ~2s → attempt 2 succeeds (200). Run `status = "success"`; `result` holds the success payload only. Logs show `attempt_start` (×2, the 2nd `is_retry=true`), `attempt_succeeded`, and `run_complete` with `attempts=2`.

### Scenario D — Retries exhausted (US2)

1. Target always returns `500`. `retry = {"backoff_interval": [2, 5, 8], "retryable_errors": [500, 502]}`.
2. **Expected**: 1 initial + 3 retries = 4 attempts, waits 2s, 5s, 8s between them. Run `status = "error"` with `result.error_text` set. Final log is `last_retry_failed` explicitly stating it was the last retry and it failed; `run_failed` log carries `attempts=4`. No per-attempt array in the run row.

### Scenario E — Non-retryable error (US2)

1. Target returns `403`. `retry = {"backoff_interval": [2, 5], "retryable_errors": [500]}`.
2. **Expected**: single attempt, immediate `status = "error"`, no backoff waits, log `attempt_failed_non_retryable` with `status_code=403` and `reason="status_not_in_retryable"`.

### Scenario F — No retry configured (US2 / backward compat)

1. Create a schedule with no `retry`. Target returns `500`.
2. **Expected**: single attempt, `status = "error"`, no retries — identical to pre-feature behaviour.

### Scenario G — Validation (FR-013)

1. `POST` with `timeout: 0` → **422**.
2. `POST` with `retry.backoff_interval: []` → **422**.
3. `POST` with `retry.retryable_errors: [999]` → **422**.

### Scenario H — Observability (US3 / SC-005)

1. Run Scenario D and inspect logs.
2. **Expected**: from logs alone you can determine the applied `timeout`, each attempt number, which attempts were retries, the backoff waited, the triggering status code, and that the final attempt was the last retry and failed. Each entry carries `schedule_id` and `run_id`.

## Done / acceptance

All automated checks pass and Scenarios A–H behave as described. Cross-reference: [spec.md](./spec.md) acceptance scenarios, [contracts/schedule-properties.md](./contracts/schedule-properties.md), [data-model.md](./data-model.md).

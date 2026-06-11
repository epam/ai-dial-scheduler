# Quickstart: Schedule Run Tracking

Validates that every fired non-heartbeat job produces a `schedule_runs` row that transitions
`in_progress` → `success`/`error`, and that the heartbeat produces none.

## Prerequisites

- `uv sync`
- Running PostgreSQL reachable via the configured DB settings.
- Schema applied: `uv run alembic upgrade head` (the `schedule_runs` table already exists from
  migration `20260610_1947_092b5f51aa98` — **no new migration in this feature**).

## Quality gates (run all before marking complete)

```bash
uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

## Scenario 1 — Successful run is persisted (FR-001, FR-003, FR-005, SC-001/003)

1. Fire a REST/SSE/chat_completion job through a real `AsyncIOScheduler` (see
   `tests/integration/test_schedule_run_lifecycle.py`).
2. **Before** completion: a row exists with `status="in_progress"`, `end_time IS NULL`,
   `result IS NULL`, the correct `schedule_id`, and `schedule_snapshot` = the job kwargs.
3. **After** completion: the same row (same `id` = `run_id`) has `status="success"`,
   `end_time` set, and `result` holding the executor's returned object.
4. Logs include `run_start`, `run_recorded_success` (assert via `structlog.testing.capture_logs`).

## Scenario 2 — Failed run is persisted with its reason (FR-006, FR-008, SC-004)

1. Fire a job whose target returns a non-2xx / raises (mock the transport), **or** a job with
   invalid kwargs (e.g. REST without `method`).
2. The row transitions to `status="error"`, `end_time` set, and
   `result == {"error_text": "<exception message>"}`.
3. Logs include `run_failed` and `run_recorded_error`.

## Scenario 3 — Heartbeat creates no row (FR-002, SC-005)

1. Let the heartbeat job fire.
2. Assert **no** `schedule_runs` row is created for it and the success listener logs
   `run_persist_skip` (heartbeat `retval` is `None`).

## Scenario 4 — Missing start row surfaces as a discrepancy (FR-010)

1. Simulate `mark_success`/`mark_error` returning `None` (no matching row).
2. Assert the listener logs `run_record_missing` with `run_id`/`schedule_id` and does **not**
   raise or crash the worker.

## Correlation check (SC-007)

Given a `run_id`, `SELECT * FROM schedule_runs WHERE id = '<run_id>'` returns exactly one row
whose `status`, `end_time`, and `result` reflect the outcome.

## References

- Result shapes & `RunOutcome`: [contracts/run-outcome.md](./contracts/run-outcome.md)
- Repository API: [contracts/schedule-run-repository.md](./contracts/schedule-run-repository.md)
- Listener behavior & logged events: [contracts/run-listeners.md](./contracts/run-listeners.md)
- Entity/state model: [data-model.md](./data-model.md)

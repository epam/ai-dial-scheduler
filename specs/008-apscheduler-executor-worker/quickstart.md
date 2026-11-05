# Quickstart: Validate the APScheduler Executor Worker

Validation/run guide proving the feature works end-to-end. Implementation details live in
`tasks.md` and the source; this guide only shows how to exercise and verify behaviour.

## Prerequisites

- `uv sync` (after `openai` and `httpx-sse` are added to `pyproject.toml`).
- A reachable PostgreSQL (the shared DB) with migrations applied: `uv run alembic upgrade head`.
- Settings provided (env): DB connection, `DIAL_URL`, `DIAL_API_KEY`, `NOOP_INTERVAL_SECONDS`,
  `HTTP_TIMEOUT_SECONDS`. Never commit secrets.

## 1. Heartbeat (liveness — User Story 3)

Start the worker with no task jobs scheduled:

```bash
uv run python entrypoint_worker.py
```

**Expected**: a structured log record every `NOOP_INTERVAL_SECONDS` containing the job name and
an ISO-8601 UTC `executed_at`, and nothing else. The heartbeat lives in an in-memory jobstore,
so it never appears in the shared `apscheduler_jobs` table. (SC-005)

## 2. Execute each target type (User Story 1)

Add a job to the shared store the way the creator service does — referencing
`EXECUTE_SCHEDULED_JOB_REF` with kwargs `{**properties, "schedule_id": ...}` where `properties`
is a valid `ScheduleProperties` payload. Let it fire (no `schedules` row is needed; the worker
reads config from the kwargs).

- **`rest`**: `method` set, `url` reachable, returns 2xx → one logged response record with the
  `run_id`/`schedule_id`.
- **`sse`**: target streams `text/event-stream` → one logged record per event as it arrives,
  keyed by event name.
- **`chat_completion`** non-stream: `payload.stream` absent/false → one logged result record.
- **`chat_completion`** stream: `payload.stream = true` → one logged record per chunk.

**Expected**: correct executor selected per `target_type` (SC-003); streamed records counted
equal to chunks/events received and logged as they arrive (SC-004); every record carries
`run_id` and `schedule_id`.

## 3. Failure paths (User Story 2)

Fire jobs that trigger each failure and confirm exactly one logged failure with both ids
(SC-001, SC-002), surfaced by the `EVENT_JOB_ERROR` listener:

- **Invalid config**: the fired job's kwargs violate `ScheduleProperties` (e.g. `rest` with
  null `method`).
- **Non-2xx**: `rest`/`sse` target returns 4xx/5xx.
- **Transport error**: unreachable target / timeout.

Confirm a failure in one run does not stop a concurrently/subsequently fired run (SC-006).

## 4. Automated checks (authoritative)

```bash
uv run pytest tests/unit/worker tests/integration/test_worker_dispatch.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

**Expected**: all pass. Unit tests cover the dispatcher, each executor, validation failures,
the error listener, the heartbeat, and `WorkerContext`. The integration test fires a
job end-to-end through a real `AsyncIOScheduler`.

## 5. No persistence (SC-007)

After exercising the above, confirm no rows were written to any results store
(`schedule_runs`) by the worker — every outcome is observable only in logs.

## References

- Dispatch + failure contract: [contracts/dispatch-handler.md](./contracts/dispatch-handler.md)
- Config schema: [contracts/kwargs-schema.md](./contracts/kwargs-schema.md)
- Executor behaviours: [contracts/executor-interface.md](./contracts/executor-interface.md)
- Entities & log records: [data-model.md](./data-model.md)

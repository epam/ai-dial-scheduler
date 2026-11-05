# Quickstart: Schedule Management Endpoints

Validation guide proving the four endpoints work end-to-end. See
[contracts/schedule-endpoints.md](./contracts/schedule-endpoints.md) for full request/response
shapes and [data-model.md](./data-model.md) for schema/validation detail.

## Prerequisites

- `uv sync` completed; PostgreSQL reachable per `.env` (DB settings).
- Schema is migrated: `uv run alembic upgrade head` (tables `schedules`, `apscheduler_jobs`,
  `schedule_runs` from feature 006 must exist). **This feature adds no migration.**

## Run the services

```powershell
# API service (registers jobs; scheduler runs paused)
uv run python -m src.api            # or the project's API entrypoint

# Worker service (executes jobs) — separate terminal
uv run python -m src.worker         # or the project's worker entrypoint
```

The API scheduler starts `paused=True`; the worker is the sole executor and polls the jobstore
every ~5s.

## Scenario 1 — Create (US1, FR-001..FR-008)

```powershell
$body = @'
{
  "display_name": "Every minute ping",
  "trigger": { "cron": { "fields": { "second": "0" } } },
  "properties": {
    "target_type": "rest", "method": "GET",
    "url": "https://example.com/health", "payload": {}, "extra_headers": {}
  }
}
'@
curl.exe -s -X POST "http://localhost:5000/schedules/" -H "Content-Type: application/json" -d $body
```

**Expect**: `201` with a non-empty `id` (uuid4 string), `created_by: "mock_user_id"`, and a
non-null `next_run_time`. Within ~1 minute the **worker** log shows
`execute_scheduled_job` emitting the current UTC time and the kwargs (properties + `schedule_id`).

## Scenario 2 — Trigger / properties validation (FR-021, FR-025/026)

```powershell
# Both triggers -> 422
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "http://localhost:5000/schedules/" -H "Content-Type: application/json" -d '{"display_name":"x","trigger":{"cron":{"fields":{}},"date":"2030-01-01T00:00:00Z"},"properties":{"target_type":"rest","method":"GET","url":"https://e.com","payload":{}}}'

# rest without method -> 422
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "http://localhost:5000/schedules/" -H "Content-Type: application/json" -d '{"display_name":"x","trigger":{"date":"2030-01-01T00:00:00Z"},"properties":{"target_type":"rest","url":"https://e.com","payload":{}}}'
```

**Expect**: `422` for both; no rows created (verify with Scenario 4 count unchanged).

## Scenario 3 — Update (US2, FR-009..FR-013)

```powershell
$id = "<id from Scenario 1>"
curl.exe -s -X PUT "http://localhost:5000/schedules/$id" -H "Content-Type: application/json" -d '{"display_name":"renamed","trigger":{"date":"2999-01-01T00:00:00Z"},"properties":{"target_type":"sse","method":"POST","url":"https://example.com/stream","payload":{}}}'
```

**Expect**: `200`, `display_name: "renamed"`, recomputed `next_run_time` (the far-future date),
`updated_at` newer than `created_at`. Updating an unknown id → `404`.

## Scenario 4 — List (US3, FR-014..FR-016)

```powershell
curl.exe -s "http://localhost:5000/schedules/?limit=20&offset=0"
```

**Expect**: `200` with `count`, `limit`, `offset`, a `results` array of summaries
(`trigger_type` is `cron`/`date`), `previous: null` on the first page and `next` set when more
pages exist. Only `mock_user_id` schedules appear.

## Scenario 5 — Details (US4, FR-017/018)

```powershell
curl.exe -s "http://localhost:5000/schedules/$id"          # -> 200 full object
curl.exe -s -o NUL -w "%{http_code}`n" "http://localhost:5000/schedules/does-not-exist"  # -> 404
```

## Automated checks (quality gates)

```powershell
uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

**Expect**: unit tests cover trigger xor + properties matrix, repository CRUD + LEFT JOIN,
service create/update **rollback** paths (mocked scheduler raising on `add_job`), endpoint
status codes, and the worker job logging. All gates green.

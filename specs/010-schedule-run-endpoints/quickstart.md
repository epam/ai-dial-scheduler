# Quickstart & Validation: Schedule Run List & Run Details Endpoints

Validation guide for the two read-only run endpoints. For field-level detail see
[data-model.md](./data-model.md) and [contracts/](./contracts/).

## Prerequisites

- `uv sync` completed; PostgreSQL reachable with schema migrated (`uv run alembic upgrade head`).
- Feature 009 (Schedule Run Tracking) present — runs are produced by the worker; these endpoints
  only read them.

## Quality gates (run from repo root)

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml
```

## Automated test scenarios (must exist and pass)

### Unit — endpoint (`tests/unit/api/v1/endpoints/test_runs.py`, mocked service)
- `list_runs` returns the service's `Page` and 200.
- `get_run` returns the service's `RunResponse` and 200.
- `ScheduleNotFoundError` → 404; `ScheduleRunNotFoundError` → 404.
- Malformed `run_id` → 422 (FastAPI path validation).

### Unit — service (`tests/unit/lib/services/test_schedule_run_service.py`, mocked repos)
- `list_runs`: ownership ok → returns `Page[RunListItem]` (mapping `created_at`→`start_time`).
- `list_runs`: ownership `None` → raises `ScheduleNotFoundError` (run repo never called).
- `list_runs`: owned schedule with zero runs → empty `Page`, `count = 0`.
- `get_run`: ownership ok + run found → `RunResponse` with embedded schedule summary.
- `get_run`: ownership `None` → `ScheduleNotFoundError`.
- `get_run`: run `None` (missing or wrong schedule) → `ScheduleRunNotFoundError`.

### Unit — repository (`tests/unit/lib/repositories/test_schedule_run_repository.py`)
- `list_for_schedule`: returns `(count, rows)`; verifies `schedule_id` filter, `created_at DESC, id DESC`
  ordering, and limit/offset.
- `get_for_schedule`: returns the run only when `id` and `schedule_id` both match; `None` otherwise.

### Integration (`tests/integration/test_runs_api.py`, real DB)
End-to-end over `httpx.AsyncClient`, seeding `schedule_runs` rows directly:
1. Create a schedule (existing POST endpoint), seed several runs across statuses.
2. `GET /v1/schedules/{id}/runs` → 200, `count` correct, page items carry id/status/start_time/end_time,
   `next`/`previous` present at page boundaries.
3. Paging through all pages visits each run exactly once (SC-002).
4. `GET /v1/schedules/{id}/runs` for a schedule with no runs → 200, `count: 0`, `results: []`.
5. `GET /v1/schedules/{id}/runs/{run_id}` → 200, full body incl. `result` and `schedule` summary;
   verify success / error / in_progress shapes (`end_time`/`result` nullability).
6. Cross-owner / unknown schedule → 404, identical body.
7. Run id belonging to a different schedule → 404.
8. Malformed `run_id` → 422.

## Manual smoke (optional)

```bash
# List runs
curl -s "http://localhost:8000/v1/schedules/<schedule_id>/runs?limit=20&offset=0" | jq

# Run details
curl -s "http://localhost:8000/v1/schedules/<schedule_id>/runs/<run_id>" | jq
```

Expected: paginated envelope for the first; full run object with embedded `schedule` for the second;
404 for a schedule/run you do not own.

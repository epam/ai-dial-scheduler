# Quickstart: Validating Multiple Cron Triggers

Prerequisites: `uv sync`, a running PostgreSQL for integration tests. The API cannot bind its default port 5000 on this machine, so run it on another port.

## 1. Unit and integration tests

```
uv run pytest tests/unit/lib/schemas/test_schedule_schemas.py tests/unit/lib/services/test_trigger_factory.py
uv run pytest tests/integration/test_schedules_api.py
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
```

Expected: all pass, including the new `crons` cases.

## 2. End-to-end scenario (manual)

1. Create a schedule with the body in [contracts/trigger-crons.md](contracts/trigger-crons.md) (Monday 08:00 and Friday 10:00 UTC).
2. `GET /v1/schedules/{id}`: `trigger.crons` has two entries; `next_run_time` is the earlier upcoming of the two moments.
3. `PUT` the same schedule with a single `cron`, then back to `crons`, then a `date`: each response reflects only the new trigger.
4. Pause: `next_run_time` is `null`. Resume: it is the earliest upcoming fire time again.
5. Negative checks (expect 422 naming the location): `crons: []`, 21 entries, `cron` and `crons` together, an invalid field in the second entry.
6. Regression: fetch a schedule created before this change; it returns and its `next_run_time` is unchanged.
7. Coinciding entries: create `crons` with two entries that share a fire time; after that time, exactly one run is recorded.

See [data-model.md](data-model.md) for the stored shapes.

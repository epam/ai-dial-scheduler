# Quickstart: Validating Per-Schedule Timezone

Prerequisites: `uv sync` and a running PostgreSQL for integration tests (`TEST_DB_URL`). The API cannot bind its default port 5000 on this machine, so run it on another port.

## 1. Automated checks

```
uv run alembic upgrade head
uv run pytest tests/unit/lib/schemas/test_schedule_schemas.py tests/unit/lib/services/test_trigger_factory.py \
              tests/unit/lib/services/test_schedule_service.py tests/unit/migrations/test_add_timezone.py
uv run pytest tests/integration/test_schedules_api.py tests/integration/test_schedule_repository.py
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
uv run pytest --cov=src
```

Expected: all pass, including the browser-list test (418 names plus `UTC`) and the DST tests.

## 2. End-to-end scenario (manual)

1. Create a schedule with the body from [contracts/schedule-timezone.md](contracts/schedule-timezone.md) (weekdays 09:00 `Europe/Berlin`). The response has `"timezone": "Europe/Berlin"`, and `next_run_time` is the next weekday 08:00Z in winter or 07:00Z in summer.
2. `GET /v1/schedules/{id}` and `GET /v1/schedules` both show `"timezone": "Europe/Berlin"`.
3. Create one with `"timezone": "Europe/Kiev"`. It reads back as `Europe/Kiev`, not `Europe/Kyiv`.
4. `PUT` the first schedule with `"timezone": "Asia/Tokyo"`. `next_run_time` becomes the next weekday 00:00Z.
5. `PUT` it again without `timezone`. The response shows `"UTC"` and `next_run_time` is the next weekday 09:00Z.
6. Pause, then resume: `next_run_time` is computed in the stored zone.
7. Negative checks, each expecting 422 at `body.timezone` and no change to the schedule: `Mars/Olympus`, `europe/berlin`, `+02:00`, `""`.
8. Regression: a schedule created before the migration reads back with `"timezone": "UTC"` and an unchanged `next_run_time`.
9. Downgrade check: `uv run alembic downgrade -1` and then `upgrade head` succeed.

## 3. DST behaviour (unit-level; not practical end to end)

- `tests/unit/lib/services/test_trigger_factory.py` pins these, using a fixed `now` around 2027-03-28 and 2027-10-31 in `Europe/Berlin`:
  - daily 02:30 fires once on each change day (03:30 local in spring, the first 02:30 in autumn);
  - hourly fires at both 02:00 occurrences in autumn;
  - daily 09:00 moves from 08:00Z to 07:00Z across spring.

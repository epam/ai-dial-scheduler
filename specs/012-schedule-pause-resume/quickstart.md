# Quickstart: Schedule Pause & Resume Endpoints

Validation guide proving the pause/resume capability end-to-end. Implementation details live
in [plan.md](./plan.md), [data-model.md](./data-model.md), and
[contracts/pause-resume-endpoints.md](./contracts/pause-resume-endpoints.md).

## Prerequisites

- `uv sync` completed; PostgreSQL reachable per `src/lib/settings.py`.
- Migrations applied: `uv run alembic upgrade head` (no new migration is added by this feature).
- A valid `Api-Key` that resolves to a user identity via the DIAL `/v1/info` endpoint.

## Automated validation (authoritative)

Run the test suites that gate this feature (written first, per Constitution III):

```bash
uv run pytest tests/unit/lib/services/test_schedule_service.py -k "pause or resume"
uv run pytest tests/integration/test_schedules_api.py -k "pause or resume"
```

Quality gates (all must pass before merge):

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src
```

**Expected**: pause/resume unit cases (owned, not-owned→`ScheduleNotFoundError`,
missing-job→`ScheduleNotFoundError`, idempotent re-pause/re-resume) and integration cases
(200 owned, 404 cross-owner, 404 unknown id, 401 unauthenticated) pass.

## Manual validation scenarios

Start the API: `uv run python -m src.api.app` (or the project's configured entry point).
Create a schedule first (see the create-schedule contract) and note its `id`.

### Scenario A — Pause an active schedule (US1)

```bash
curl -s -X POST "http://localhost:8080/v1/schedules/$SCHEDULE_ID/pause" \
  -H "Api-Key: $API_KEY"
```

**Expected**: `200 OK`; response body is the schedule with `"next_run_time": null`. A
subsequent `GET /v1/schedules/$SCHEDULE_ID` still returns the schedule (it was not deleted).

### Scenario B — Resume a paused schedule (US2)

```bash
curl -s -X POST "http://localhost:8080/v1/schedules/$SCHEDULE_ID/resume" \
  -H "Api-Key: $API_KEY"
```

**Expected**: `200 OK`; `next_run_time` is populated with the next firing from the trigger
(or `null` if the trigger has no future firings).

### Scenario C — Idempotency (US1/US2, FR-007)

Repeat Scenario A twice, then Scenario B twice.

**Expected**: every call returns `200 OK`; the schedule ends in the requested state with no
error.

### Scenario D — Owner-only access (US3, FR-004)

Using a *different* user's `Api-Key`, pause/resume `$SCHEDULE_ID`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "http://localhost:8080/v1/schedules/$SCHEDULE_ID/pause" -H "Api-Key: $OTHER_API_KEY"
```

**Expected**: `404` — identical to acting on a non-existent id
(`/v1/schedules/does-not-exist/pause` also returns `404`).

### Scenario E — Unauthenticated (FR-008)

Call pause/resume with no `Api-Key` header.

**Expected**: `401` (or the standard missing-header error); no state change.

## Done when

- Automated unit + integration suites pass and quality gates are green.
- Manual scenarios A–E produce the expected results.
- The paused schedule is observed by the worker to stop firing, and resumes firing after
  Scenario B (shared APScheduler job store).

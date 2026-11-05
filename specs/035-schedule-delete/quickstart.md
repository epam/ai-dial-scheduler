# Quickstart: Validating Schedule Deletion

Runnable validation for `specs/035-schedule-delete`. Details live in
[data-model.md](./data-model.md) and [contracts/](./contracts); this file is the run guide.

## Prerequisites

- `uv sync`
- A reachable PostgreSQL instance configured per `src/lib/settings.py` (integration tests use the
  fixtures in `tests/integration/conftest.py`).
- Migrations applied: `uv run alembic upgrade head` — must include the new
  `..._add_is_deleted_to_schedules` revision (`down_revision = d4e2b3c5f6a7`).

## 1. Quality gates

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest
```

All four must pass. Per the constitution's TDD requirement, the tests enumerated in
[data-model.md §5](./data-model.md) are written and confirmed failing **before** implementation.

## 2. Migration check

```bash
uv run alembic upgrade head
uv run alembic downgrade -1     # drops is_deleted
uv run alembic upgrade head     # re-adds and backfills
```

Expected: schedules created before the upgrade report `is_deleted = false` afterwards — the
`NOT NULL DEFAULT false` column backfills in the `ADD COLUMN` statement, so no data migration runs.

## 3. Targeted test runs

```bash
# Service branch selection, guards, and store-ordering failures
uv run pytest tests/unit/lib/services/test_schedule_service.py -k delete -v

# New repository methods and the list filter
uv run pytest tests/unit/lib/repositories/test_schedule_repository.py -v
uv run pytest tests/unit/lib/repositories/test_schedule_run_repository.py -k count -v

# Endpoint status codes, request-body immutability
uv run pytest tests/unit/api/v1/endpoints/test_schedules.py -k "delete or is_deleted" -v

# End-to-end, real DB: FK RESTRICT fallback, history survival, filtering
uv run pytest tests/integration/test_schedule_delete.py -v

# Worker pre-flight guard
uv run pytest tests/unit -k "run_skipped or schedule_deleted" -v

# MCP tool surface (the DELETE route is auto-exposed)
uv run pytest tests/unit/api/mcp/test_mcp_tools.py -v
```

## 4. Manual end-to-end walkthrough

Run the API (see `src/api/app.py`) and send an owner-identifying auth header as with any other
schedules call. `$ID` is the `id` from the create response.

### 4a. Hard delete — schedule that never ran

```bash
# create (use any example body from the POST /v1/schedules OpenAPI examples)
curl -X POST .../v1/schedules/ -H 'content-type: application/json' -d @schedule.json

curl -X DELETE .../v1/schedules/$ID -i        # expect: 204, empty body
curl        .../v1/schedules/$ID -i           # expect: 404
curl        '.../v1/schedules/?is_deleted=true' # expect: $ID absent (it is gone, not soft-deleted)
curl -X DELETE .../v1/schedules/$ID -i        # expect: 404
```

### 4b. Soft delete — schedule with run history

Create a schedule that fires soon (e.g. a cron trigger on the next minute), wait for one run to be
recorded, then:

```bash
curl        .../v1/schedules/$ID/runs -i      # expect: 200, >= 1 run — note the count
curl -X DELETE .../v1/schedules/$ID -i        # expect: 204

curl        .../v1/schedules/$ID -i           # expect: 200, "is_deleted": true, "next_run_time": null
curl        .../v1/schedules/$ID/runs -i      # expect: 200, same runs, unchanged
curl        .../v1/schedules/ -i              # expect: $ID absent (default filter)
curl        '.../v1/schedules/?is_deleted=true' -i   # expect: $ID present, is_deleted true

# frozen — every mutation rejected
curl -X DELETE .../v1/schedules/$ID -i                  # expect: 409
curl -X PUT    .../v1/schedules/$ID -d @schedule.json -i # expect: 409
curl -X POST   .../v1/schedules/$ID/pause -i            # expect: 409
curl -X POST   .../v1/schedules/$ID/resume -i           # expect: 409
```

Then confirm it never fires again: wait past two trigger intervals and re-check
`GET /v1/schedules/$ID/runs` — the run count must be unchanged.

### 4c. Field immutability

```bash
# on a live schedule, send is_deleted in the body
curl -X PUT .../v1/schedules/$LIVE_ID \
  -H 'content-type: application/json' \
  -d '{"is_deleted": true, ...rest of a valid body...}' -i
# expect: 200 and "is_deleted": false — the field is ignored, not rejected
```

### 4d. Filter validation and pagination

```bash
curl '.../v1/schedules/?is_deleted=maybe' -i          # expect: 422
curl '.../v1/schedules/?is_deleted=true&limit=1' -i   # expect: "next" link contains is_deleted=true
curl '.../v1/schedules/?limit=1' -i                   # expect: "next" link has no is_deleted
```

Also confirm `count` in a default-filtered response excludes soft-deleted schedules.

## 5. Failure-path verification (test-only)

These are unit tests rather than manual steps, but they are the checks that prove FR-017:

| Injected failure | Expected |
|------------------|----------|
| `scheduler.remove_job` raises `JobLookupError` | 204 — deletion completes, "job absent" logged |
| `scheduler.remove_job` raises anything else | 502 — **no** database write; schedule still live and still fires |
| Repository write fails after a successful unregister | Error to the caller; a second `DELETE` returns 204 |
| A run row is inserted between the count and the hard delete | `IntegrityError` caught → soft delete; run preserved; race event logged |

## 6. Documentation check

`documentation/api_contract.md` and `documentation/db_tables.md` were updated during planning with
the designed contract. After implementation, verify they match reality — specifically the DELETE
status codes, the `is_deleted` response field on all four schedule response shapes, the
`is_deleted` list query parameter, and the `schedules.is_deleted` column description. Any divergence
means either the docs or the implementation is wrong.

## 7. Observability check

Grep the structured logs of a delete for the events in [data-model.md §4](./data-model.md) — at
minimum `schedule_delete_start` and exactly one of `schedule_deleted` (with `outcome="removed"`) or
`schedule_marked_deleted` (with `outcome="marked_deleted"`), each carrying `schedule_id` and
`run_count`. This is what makes SC-008 verifiable from logs alone.

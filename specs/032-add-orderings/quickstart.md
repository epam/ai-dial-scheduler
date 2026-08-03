# Quickstart & Validation: Add Ordering for Schedules/Runs List

Validates that the schedules and runs lists can be ordered per-request, with inactive schedules
placed last for next-run ordering, stable pagination, and 422 on invalid input.

## Prerequisites

- `uv sync`
- PostgreSQL reachable (for integration tests). Env/DB config per `src/lib/settings.py`.
- Migrations current: `uv run alembic upgrade head` (no new migration is introduced by this feature).

## Automated validation (primary)

```bash
# Unit: query-param parsing, defaults, invalid value handling
uv run pytest tests/unit/api/dependencies/test_ordering.py

# Unit: repository ordering (both fields, both directions, NULLs-last, tiebreaker)
uv run pytest tests/unit/lib/repositories/test_schedule_repository.py
uv run pytest tests/unit/lib/repositories/test_schedule_run_repository.py

# Integration: real-DB ordering incl. next_run_time NULLs-last + endpoint 422
uv run pytest tests/integration/test_schedule_repository.py
uv run pytest tests/integration/test_schedules_api.py

# Full gate
uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

Expected: all pass. Ordering tests confirm the invariants in `data-model.md`.

## Manual smoke (secondary)

Run the API service, then (with a valid DIAL user token):

```bash
# Schedules by creation date, oldest first
curl -s "$BASE/v1/schedules?order_by=created_at&order_dir=asc" -H "$AUTH"

# Schedules by next run date, soonest first — paused/inactive schedules appear last
curl -s "$BASE/v1/schedules?order_by=next_run_time&order_dir=asc" -H "$AUTH"

# Schedules by name, A→Z (case-insensitive)
curl -s "$BASE/v1/schedules?order_by=name&order_dir=asc" -H "$AUTH"

# Runs by creation date, newest first (default) and oldest first
curl -s "$BASE/v1/schedules/$SID/runs" -H "$AUTH"
curl -s "$BASE/v1/schedules/$SID/runs?order_dir=asc" -H "$AUTH"

# Invalid ordering → 422
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/v1/schedules?order_by=bogus" -H "$AUTH"   # 422
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/v1/schedules/$SID/runs?order_by=next_run_time" -H "$AUTH"  # 422
```

## Expected outcomes (acceptance)

- Schedules ordered by `created_at` asc/desc match chronological order (US1).
- Schedules ordered by `next_run_time`: active schedules in soonest/latest order; all inactive
  (no next run) schedules after the active ones, in both directions (US2, FR-003).
- Schedules ordered by `name`: case-insensitive alphabetical order in both directions (US3, FR-004).
- Runs ordered by `created_at` asc/desc match chronological order (US4).
- Following `next` preserves the chosen ordering; no rows skipped or duplicated across pages (SC-005).
- Invalid `order_by`/`order_dir` → HTTP 422 with no results (SC-006).

## References

- Query params & semantics: `contracts/schedules-list-ordering.md`, `contracts/runs-list-ordering.md`
- Sort keys, enums, value objects, invariants: `data-model.md`
- Decisions & rationale: `research.md`

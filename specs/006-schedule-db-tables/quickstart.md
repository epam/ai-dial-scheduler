# Quickstart: Validate Schedule & Run Persistence Tables

A run/validation guide proving the feature works end-to-end. Implementation code lives in
`src/lib/...`; see [data-model.md](./data-model.md) and
[contracts/schema-contract.md](./contracts/schema-contract.md) for the exact schema.

## Prerequisites

- `uv sync` completed.
- A PostgreSQL instance reachable via the project's `DatabaseSettings` (see `src/lib/settings.py`).
- Branch `006-schedule-db-tables` checked out.

## 1. Generate the migration (autogenerate only)

```powershell
uv run alembic revision --autogenerate -m "create schedules and schedule_runs tables"
```

**Expected**:
- A new file appears in `migrations/versions/` (named per `alembic.ini` `file_template`).
- Its `upgrade()` contains `op.create_table("schedules", ...)`,
  `op.create_table("schedule_runs", ...)`, `op.create_index("IX_schedule_runs_schedule_id", ...)`,
  the `CK_schedule_runs_status` CHECK, and the `FK_..._schedules` FK with `ondelete='RESTRICT'`.
- It contains **no** reference to `apscheduler_jobs`.
- ⚠️ **Do not edit the generated file.** If it is wrong, fix the models and regenerate.

## 2. Apply and reverse the migration (FR-018, SC-005)

```powershell
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

**Expected**: All three commands succeed. `downgrade` then re-`upgrade` proves full
reversibility with no orphaned objects (confirms the no-native-enum decision, R4).

## 3. Validate via the test suite (TDD artifacts)

```powershell
uv run pytest tests/lib/models/test_schedule.py tests/lib/models/test_schedule_run.py tests/integration/test_schedule_run_join.py
```

These tests (written before implementation per Principle III) assert:

| Test focus | Maps to |
|------------|---------|
| `schedules` columns, types, `id` length 191, PK | FR-001..004, SC-001 |
| `schedule_runs` columns, nullable `end_time`/`result`, UUID PK | FR-007..009, FR-011 |
| `status` CHECK rejects an out-of-set value | FR-010, SC-002 |
| FK `RESTRICT`: deleting a schedule with runs is rejected | FR-014, SC-004 |
| LEFT JOIN returns `next_run_time`, keeps metadata when engine row missing/paused | FR-005/006, SC-001 |
| `schedule_snapshot` unchanged after parent schedule edited | FR-012, SC-003 |

## 4. Manual join smoke check (optional)

With a `schedules` row whose `id` matches an `apscheduler_jobs.id`:

```text
SELECT s.id, s.display_name, j.next_run_time
FROM schedules s
LEFT JOIN apscheduler_jobs j ON s.id = j.id;
```

(Issued through the project's SQLAlchemy session / the read-only `apscheduler_jobs` Core
table reference — not raw SQL in production code.)

**Expected**: every `schedules` row returned; `next_run_time` populated for active jobs,
`NULL` for paused jobs or schedules without an engine row.

## Quality gates (must pass before completion)

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src
```

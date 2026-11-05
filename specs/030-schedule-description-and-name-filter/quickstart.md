# Quickstart & Validation: Schedule Description & Name Filter

Validation guide proving the feature works end-to-end. Implementation details live in `tasks.md` / the implementation phase; this is a run/verify guide.

## Prerequisites

- Dependencies installed: `uv sync`
- PostgreSQL reachable (per project settings).
- Feature branch checked out: `030-schedule-description-and-name-filter`.

## Setup

Apply the new migration (adds the `description` column):

```bash
uv run alembic upgrade head
```

Verify the column exists:

```bash
uv run alembic current   # should show the add_description_to_schedules revision at head
```

## Automated checks (quality gates)

```bash
uv run pytest                          # unit + integration tests
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

Expected: all pass. New tests cover description length validation, name-filter matching, and filter composition.

## Manual end-to-end validation

Start the API service, then exercise the scenarios below (owner scope comes from the auth identity; adjust auth headers per environment).

### Scenario A — Create with description, see it on details (User Story 1 & 2)

1. `POST /schedules/` with a `description` field set.
2. Confirm `201` and the response echoes `description`.
3. `GET /schedules/{id}` → response includes the same `description`.

**Expected**: description persisted and returned unchanged.

### Scenario B — Over-length description rejected (FR-006, SC-002)

1. `POST /schedules/` with a 1001-character `description`.
2. **Expected**: `422`, validation error on `body.description`, no schedule created.
3. A 1000-character description on the same request → `201` (boundary accepted).

### Scenario C — Description on list rows (FR-008)

1. `GET /schedules/` → each row includes a `description` field (value or `null`).

**Expected**: list rows carry the description.

### Scenario D — Filter list by name (User Story 3)

1. Create schedules named e.g. "Daily report" and "Hourly ping".
2. `GET /schedules/?name=report` → only "Daily report" returned.
3. `GET /schedules/?name=REPORT` → same result (case-insensitive).
4. `GET /schedules/?name=nomatch` → empty result set, `200` (no error).
5. `GET /schedules/` (no `name`) → full owner-scoped list.

**Expected**: substring, case-insensitive, owner-scoped matching.

### Scenario E — Composition with metadata + pagination (FR-013)

1. `GET /schedules/?name=report&metadata__tenant=hcscd&limit=10&offset=0`.
2. **Expected**: only schedules matching name AND metadata AND owner; pagination totals reflect the filtered set.

### Scenario F — Trigger on list rows (User Story 4, FR-016/FR-017)

1. Create one cron-triggered and one date-triggered schedule.
2. `GET /schedules/` → each row includes both `trigger_type` and the full `trigger` object.
3. Compare each row's `trigger` to the same schedule's `GET /schedules/{id}` `trigger`.

**Expected**: list `trigger` is identical to the details `trigger`; `trigger_type` is still present.

### Scenario G — Backward compatibility (FR-015)

1. For a schedule created before the migration (or a row with NULL description), `GET /schedules/{id}` and `GET /schedules/` → `description` is `null`, no error.

## References

- Schema/endpoint delta: [contracts/schedules-api.md](./contracts/schedules-api.md)
- Field lifecycle & validation rules: [data-model.md](./data-model.md)
- Design decisions: [research.md](./research.md)

# Quickstart & Validation: Schedule Metadata & Filtering

Validates the feature end-to-end against the spec. See [contracts/schedules-metadata.md](./contracts/schedules-metadata.md) for the API shape and [data-model.md](./data-model.md) for storage details.

## Prerequisites

- `uv sync` completed; PostgreSQL reachable per project settings.
- API service runnable locally; a valid auth identity available for the owner-scoped endpoints.

## Setup

```bash
# Apply the new migration (adds the JSONB `metadata` column + GIN index)
uv run alembic upgrade head
```

## Quality gates (run before declaring done)

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml
```

## Scenario 1 — Create with metadata, read it back (FR-001/002/003/006, SC-001)

1. `POST /schedules/` with a body that includes `"metadata": {"tenant": "hcscd", "env": "prod"}`.
2. Note the returned `id`; confirm the create response includes the same `metadata`.
3. `GET /schedules/{id}` → response `metadata` equals `{"tenant": "hcscd", "env": "prod"}` exactly.

**Expected**: full key/value fidelity on the round trip.

## Scenario 2 — Create without metadata (FR-005)

1. `POST /schedules/` with no `metadata` field.
2. `GET /schedules/{id}` → `metadata` is `{}` (empty object, not null), no error.

## Scenario 3 — Update replaces metadata (FR-004)

1. For the Scenario 1 schedule, `PUT /schedules/{id}` with `"metadata": {"team": "platform"}`.
2. `GET /schedules/{id}` → `metadata` is `{"team": "platform"}` (prior `tenant`/`env` gone — full replacement).

## Scenario 4 — Single-key filter (FR-007, SC-002)

1. Ensure two owned schedules exist: one with `{"tenant": "hcscd"}`, one with `{"tenant": "other"}`.
2. `GET /schedules/?metadata__tenant=hcscd`.

**Expected**: only the `hcscd` schedule in `results`; `count == 1`.

## Scenario 5 — Multi-key AND filter (FR-008, SC-003)

1. Schedules with `{"tenant":"hcscd","env":"prod"}` and `{"tenant":"hcscd","env":"dev"}`.
2. `GET /schedules/?metadata__tenant=hcscd&metadata__env=prod`.

**Expected**: only the `prod` schedule returned.

## Scenario 6 — String-only matching (FR-009, edge case)

1. Create a schedule with `"metadata": {"count": 5}` (numeric value).
2. `GET /schedules/?metadata__count=5`.

**Expected**: empty `results` (numeric stored value is not matched by a string filter).

## Scenario 7 — Unmatched key and no-filter baseline (FR-010/013, SC-004)

1. `GET /schedules/?metadata__nonexistent=whatever` → `200`, empty `results`, no error.
2. `GET /schedules/` (no filter) → identical to pre-feature owner-scoped paginated list.

## Scenario 8 — Owner scoping with filter (FR-011)

1. As user A, create a schedule with `{"tenant":"hcscd"}`.
2. As user B, `GET /schedules/?metadata__tenant=hcscd` → does **not** include user A's schedule.

## Scenario 9 — Pagination composes with filter (FR-012)

1. Create several matching schedules; request `GET /schedules/?metadata__tenant=hcscd&limit=2&offset=0`.

**Expected**: `count` = total matching, `results` length ≤ 2, `next` link present when more remain.

## Scenario 10 — Backward compatibility (FR-014, SC-005)

1. A schedule that existed before the migration is retrievable via `GET /schedules/{id}` and shows `metadata == {}`.
2. It appears in the unfiltered list and is correctly excluded by any non-empty `metadata__*` filter.

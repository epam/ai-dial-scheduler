# Implementation Plan: Schedule Metadata & Filtering

**Branch**: `019-schedule-metadata-filtering` | **Date**: 2026-06-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/019-schedule-metadata-filtering/spec.md`

## Summary

Add a JSONB `metadata` column to the `schedules` table holding a map of string keys to arbitrary JSON values. Expose `metadata` on the create/update request bodies and on the schedule details/create/update responses. Allow the schedules list endpoint to be filtered by repeated `metadata__<key>=<value>` query parameters, matching only entries whose stored JSON value is a **string** exactly equal to the supplied value, combined with AND semantics, while preserving owner-scoping and pagination.

Technical approach: store the column under a non-reserved ORM attribute name (`schedule_metadata`) mapped to DB column `metadata` (SQLAlchemy reserves `metadata` on declarative models). Filter with the PostgreSQL JSONB containment operator `@>`, which is inherently type-aware (a string filter never matches a numeric/boolean/object value) and GIN-indexable, satisfying FR-009 without per-key type checks.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, SQLAlchemy/SQLModel, Pydantic v2, Alembic, APScheduler (worker — unaffected here)

**Storage**: PostgreSQL (JSONB column + GIN index)

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server (containerized API service)

**Project Type**: Web service (FastAPI API service over PostgreSQL; `src/api`, `src/lib`, `src/worker`)

**Performance Goals**: List filtering adds no measurable latency for typical metadata sizes; containment queries use a GIN index on the `metadata` column.

**Constraints**: No raw SQL (SQLAlchemy expression API only); async I/O throughout; `metadata` is a reserved SQLAlchemy declarative attribute and MUST NOT be used as the ORM attribute name; backward compatibility for pre-existing rows (server default `'{}'`).

**Scale/Scope**: One new column, one migration, request/response schema additions, one new owner-scoped query-param filter on the existing list endpoint. No worker changes.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Reliability First**: Migration uses a NOT NULL column with server default `'{}'`, so existing rows backfill safely and no data is lost. Filter parsing rejects nothing destructive (unknown keys → empty result, not error). PASS.
- **II. Observability (NON-NEGOTIABLE)**: No new scheduling decisions are introduced (no enqueue/dequeue/retry). Existing structured logging via `src.lib.utils.logging.logger` is reused; metadata values (potential business data) are NOT logged. No `print()`. PASS.
- **III. Test-First (NON-NEGOTIABLE)**: TDD — unit tests for schema validation, repository containment filtering, service mapping, and query-param parsing will be written and confirmed failing before implementation; integration tests cover create-with-metadata → details and list-with-filter. PASS (enforced at implement phase).
- **IV. Contract-Driven API**: `metadata` is added to the versioned FastAPI OpenAPI contract (request/response schemas). The `metadata__<key>` filter is documented in `contracts/`. Internal repo/service interfaces use typed signatures and Pydantic models — no ad-hoc dicts across boundaries beyond the existing JSONB payloads. PASS.
- **V. Simplicity & YAGNI**: Uses the native JSONB `@>` operator instead of custom per-key type-checking SQL; no new abstraction layers. Filter parsing is a single dependency. Function/method parameter counts stay within 2–3 or use Pydantic/keyword grouping consistent with the existing repository. PASS.

**Result**: PASS — no violations; Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/019-schedule-metadata-filtering/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── schedules-metadata.md
├── checklists/
│   └── requirements.md  # Created by /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created here)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── dependencies/
│   │   └── metadata_filter.py        # NEW: parse metadata__<key> query params into a filter map
│   └── v1/
│       └── endpoints/
│           └── schedules.py          # MODIFY: list endpoint accepts metadata filter dependency
└── lib/
    ├── models/
    │   └── schedule.py               # MODIFY: add schedule_metadata attr → DB column "metadata"
    ├── schemas/
    │   └── schedule.py               # MODIFY: metadata on ScheduleRequest + ScheduleResponse
    ├── repositories/
    │   └── schedule_repository.py    # MODIFY: list() accepts containment filter (@> JSONB)
    └── services/
        └── schedule_service.py       # MODIFY: persist/return metadata; pass filter to repo

migrations/versions/
└── <new>_add_metadata_to_schedules.py  # NEW: add JSONB "metadata" column + GIN index

tests/
├── api/
│   ├── dependencies/test_metadata_filter.py   # NEW
│   └── v1/endpoints/test_schedules.py         # MODIFY/ADD
├── lib/
│   ├── repositories/test_schedule_repository.py  # MODIFY/ADD
│   ├── services/test_schedule_service.py         # MODIFY/ADD
│   └── schemas/test_schedule.py                  # MODIFY/ADD (if present)
└── integration/                                   # ADD: create-with-metadata + list-filter flow
```

**Structure Decision**: Follows the established three-layer split — `api/` (FastAPI routing + dependency injection for query-param parsing), `lib/` (models, schemas, repositories, services), and Alembic migrations under `migrations/versions/`. The worker service is untouched. The metadata-filter query parsing lives as a FastAPI dependency under `src/api/dependencies/` because it is HTTP-transport concern (reading `Request.query_params`), keeping `lib/` transport-agnostic.

## Complexity Tracking

> No constitution violations — section intentionally empty.

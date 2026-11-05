# Implementation Plan: Schedule Description & Name Filter

**Branch**: `030-schedule-description-and-name-filter` | **Date**: 2026-07-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/030-schedule-description-and-name-filter/spec.md`

## Summary

Add an optional, length-capped free-text `description` to schedules and expose it on both the details and list responses; add a case-insensitive substring `name` filter to the schedules list that composes with the existing pagination and metadata filters; and expose each schedule's full `trigger` definition on the list rows (in addition to the existing `trigger_type`). The approach mirrors the existing metadata feature end-to-end: a new nullable column via Alembic migration, a model field, request/response schema fields with a Pydantic `max_length` validator, a repository-level `ILIKE` filter, and a FastAPI query parameter on the list endpoint. The trigger addition is a pure response-shape change — the value is already loaded on the ORM model and returned on details today.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, SQLAlchemy/SQLModel, Pydantic v2, Alembic, APScheduler

**Storage**: PostgreSQL (schedules table; `display_name` already indexed as a plain column, new `description` column)

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server (API service container)

**Project Type**: Web service (API + background worker; this feature touches the API service and shared `lib/` only)

**Performance Goals**: Name filter must not regress list-endpoint latency for typical owner-scoped result sets (hundreds of schedules per owner); substring search runs against an owner-scoped, already-filtered set.

**Constraints**: Description max length = 1000 characters (measured in characters). Name filter = case-insensitive substring (`ILIKE '%term%'`). Backward compatible: pre-existing schedules have NULL/absent description.

**Scale/Scope**: Additive change to one entity and one list query. No worker, trigger, or execution changes.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Reliability First**: PASS — additive nullable column with no data backfill risk; migration is reversible. Description length is validated at the schema boundary before persistence. Name filter reuses the existing owner-scoped query path.
- **II. Observability**: PASS — no new scheduling decisions introduced; existing structured logging on create/update is unchanged. No new log points required (CRUD field addition).
- **III. Test-First (NON-NEGOTIABLE)**: PASS — unit tests (schema validation for length bounds; repository name filtering; service mapping of description onto list/detail) and integration tests (create-with-description round trip, list name filter, filter composition with metadata + pagination) will be written before implementation during `/speckit-implement`.
- **IV. Contract-Driven API**: PASS — request/response schema changes are captured in `contracts/` and surface automatically via FastAPI OpenAPI. Change is additive/backward-compatible (new optional request field, new response/list fields including the list `trigger`, new optional query param) — no major version bump required.
- **V. Simplicity & YAGNI**: PASS — no new abstractions. Description is a plain column; name filter is one new optional parameter threaded through the existing `list` signature. No new services, clients, or modules.

**Result**: PASS — no violations; Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/030-schedule-description-and-name-filter/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── schedules-api.md
├── checklists/
│   └── requirements.md  # Created by /speckit-specify
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── dependencies/
│   │   └── name_filter.py          # NEW: parse ?name=<term> query param (optional)
│   └── v1/endpoints/
│       └── schedules.py            # MODIFY: add name filter dependency to list_schedules
└── lib/
    ├── models/
    │   └── schedule.py             # MODIFY: add nullable description column
    ├── schemas/
    │   └── schedule.py             # MODIFY: description on ScheduleRequest (max_length),
    │                               #         ScheduleResponse, ScheduleListItem;
    │                               #         add full `trigger` to ScheduleListItem
    ├── repositories/
    │   └── schedule_repository.py  # MODIFY: create/update carry description; list ILIKE name filter
    └── services/
        └── schedule_service.py     # MODIFY: thread description + name_filter through create/update/list

migrations/versions/
└── <timestamp>_add_description_to_schedules.py   # NEW: add nullable description column

tests/
├── lib/
│   ├── schemas/test_schedule.py            # description length validation
│   ├── repositories/test_schedule_repository.py  # name ILIKE filter + composition
│   └── services/test_schedule_service.py   # description mapping on list/detail
└── api/v1/endpoints/test_schedules.py      # integration: create/list/filter
```

**Structure Decision**: Single web-service repo per the constitution's `src/api` + `src/lib` layout. This feature is confined to the API service and shared `lib/`; the background worker is untouched. The name filter follows the established dependency-injection pattern used by `parse_metadata_filter` (`src/api/dependencies/metadata_filter.py`), and the description follows the same field-lifecycle path as `schedule_metadata`.

## Complexity Tracking

> No Constitution Check violations — table intentionally omitted.

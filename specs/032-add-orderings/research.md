# Phase 0 Research: Add Ordering for Schedules/Runs List

All Technical Context items are resolved from the existing codebase; no external unknowns remain.

## R1. Where "next run date" comes from (and what "inactive" means)

- **Decision**: Order schedules by next run date using the outer-joined
  `apscheduler_jobs.c.next_run_time` column (an epoch float in the APScheduler job store),
  which `ScheduleRepository.list` already selects (`schedule_repository.py:152-153`). Treat
  "inactive/paused" as `next_run_time IS NULL` and force those rows last with SQLAlchemy
  `nulls_last()`.
- **Rationale**: The `Schedule` model has **no** `next_run_time` and **no** active/paused
  column — pause/resume operate only on the APScheduler job store, which represents a paused (or
  never-scheduled) job as `next_run_time = NULL` (`_get_with_next_run_time`,
  `schedule_repository.py:25-42`). The join is already present in `list`, so ordering needs no new
  join. `nulls_last()` makes the "inactive to the end" rule explicit and direction-independent,
  directly satisfying FR-003 / SC-002.
- **Alternatives considered**: (a) Adding an `is_active`/`next_run_time` column to `schedules` —
  rejected (YAGNI + duplicates engine-owned state, migration + sync burden). (b) Sorting in Python
  after fetch — rejected (breaks with limit/offset pagination; must sort in-DB before paginating).
  (c) Relying on default NULL ordering — rejected (Postgres defaults NULLs *first* for ASC, so the
  "always last" rule must be explicit).

## R2. How to expose the ordering parameters (query-param pattern)

- **Decision**: Add a typed FastAPI dependency `src/api/dependencies/ordering.py` using
  `Query(default=...)` fields, mirroring the existing `Pagination` dataclass
  (`dependencies/pagination.py:9-15`). Two params: `order_by` (enum) and `order_dir` (enum:
  `asc`/`desc`). Consumed via `Annotated[..., Depends()]` in the endpoints.
- **Rationale**: `Pagination` is the established pattern for typed list query params and yields
  automatic OpenAPI documentation + 422 validation for free (FR-007 / SC-005). The dynamic-key
  `Request`-parsing dependencies (`name_filter.py`, `metadata_filter.py`) exist only because their
  keys are dynamic; ordering keys are a fixed enum, so the typed dataclass approach is cleaner and
  self-documenting.
- **Alternatives considered**: Manual `Request` parsing (rejected — no automatic validation/docs,
  unneeded here); a single combined `sort` string like `-created_at` (rejected — less
  discoverable, needs custom parsing/validation, YAGNI vs. two explicit enum params).

## R3. Enum design for sort field and direction

- **Decision**: Add three `StrEnum`s to `src/lib/db/enums.py`: `SortDirection` (`asc`, `desc`),
  `ScheduleSortField` (`created_at`, `next_run_time`, `name`), `RunSortField` (`created_at`).
  Surface them directly as query-param / Pydantic field types. The `name` field maps to
  `schedules.display_name` and is ordered case-insensitively (`func.lower(display_name)`).
- **Rationale**: `enums.py` is the established home for `StrEnum`s (e.g. `ScheduleRunStatus`,
  `TriggerType`), and `ScheduleRunStatus` is already used directly as a Pydantic field type
  (`RunListItem`), so an invalid value produces a clean 422. Separate field enums per resource keep
  each endpoint's allowed values honest (runs cannot be sorted by `next_run_time`).
- **Alternatives considered**: A single shared `SortField` enum for both resources (rejected — would
  falsely advertise `next_run_time` on the runs endpoint, violating FR-007's "reject unsupported
  field").

## R3a. Name ordering — field and case handling

- **Decision**: "Order by name" maps to `schedules.display_name`, sorted case-insensitively with
  `func.lower(display_name)`, plus the PK tiebreaker. Name ordering is exposed on **schedules
  only**; the runs endpoint does not gain a name field.
- **Rationale**: `Schedule.display_name` is the only user-facing name and is already surfaced in
  `ScheduleListItem`. Case-insensitive ordering matches user expectation (`"apple"` next to
  `"Apple"`) and makes the case-differing edge case deterministic. Runs have no name column and are
  always scoped to a single schedule (they would all share one name), so name ordering is
  meaningless there and is intentionally excluded (confirmed with the requester).
- **Alternatives considered**: (a) Case-sensitive/byte ordering (rejected — surfaces
  locale/case artifacts, poorer UX). (b) Adding a name/label column to `ScheduleRun` (rejected —
  migration + population burden for no clear value; out of scope). (c) A DB collation change
  (rejected — broader impact than needed; `func.lower` is local and sufficient).

## R4. Deterministic ordering across pagination (tiebreaker)

- **Decision**: Every ordering clause appends the table primary key as the final sort key. Schedules
  → `... , schedules.id <dir>`; runs already do `created_at DESC, id DESC` — this becomes
  `created_at <dir>, id <dir>`.
- **Rationale**: `created_at` (and `next_run_time`) are not unique; without a unique tiebreaker,
  limit/offset pages can skip or duplicate rows when values tie (FR-006 / SC-004). The runs
  repository already models this pattern (`schedule_run_repository.py:80`).
- **Alternatives considered**: Keyset/cursor pagination (rejected — the whole system uses
  limit/offset; out of scope). No tiebreaker (rejected — non-deterministic, fails SC-004).

## R5. Default ordering (no param supplied)

- **Decision**: Default `order_by = created_at`, `order_dir = desc` for both lists — identical to
  the current hardcoded behavior (`schedule_repository.py:155`, `schedule_run_repository.py:80`).
- **Rationale**: Preserves existing behavior exactly (FR-005), so the change is purely additive and
  no existing client is affected; matches the spec's assumed default (newest first).
- **Alternatives considered**: Default ascending (rejected — would silently change current output
  order and break existing expectations/tests).

## R6. Preserving the ordering across `next`/`previous` page links

- **Decision**: Extend `Paginator` (`src/lib/schemas/pagination.py:22-45`) so `paginate()` appends
  the active `order_by`/`order_dir` to the generated `next`/`previous` URLs (currently only
  `limit`/`offset` are echoed, lines 32-37). The endpoint/service passes the chosen ordering into
  the paginator.
- **Rationale**: If a client follows `next` without the sort params, the order would silently reset
  and pagination would become inconsistent (violates SC-004 across pages). Carrying the params keeps
  paginated traversal stable.
- **Alternatives considered**: Leaving links unchanged (rejected — inconsistent order on page 2+);
  documenting "clients must re-send params" (rejected — fragile, contradicts SC-004). Keeping the
  `Paginator` generic: pass an optional pre-formatted extra-query string so `Paginator` stays
  resource-agnostic.

## R7. Composition with existing filtering and pagination

- **Decision**: Apply ordering as `.order_by(...)` between the existing `.where(*conditions)` and
  `.limit().offset()` in both repositories. Filtering conditions and the count query are unchanged.
- **Rationale**: Ordering must not change *which* rows match (FR-008). The count query has no
  `order_by`; only the page query is reordered. This is a drop-in replacement for the existing
  hardcoded `.order_by(created_at.desc())`.
- **Alternatives considered**: None needed — this is the minimal, correct placement.

## Summary of resolved unknowns

| Topic | Resolution |
|-------|-----------|
| Next-run source | Outer-joined `apscheduler_jobs.next_run_time`; NULL = inactive → `nulls_last()` |
| Name source | `schedules.display_name`, case-insensitive `func.lower(...)`; schedules only |
| Param surface | Typed `ordering.py` dependency (like `Pagination`); enum params → 422 on bad value |
| Enums | `SortDirection`, `ScheduleSortField` (+`name`), `RunSortField` in `lib/db/enums.py` |
| Determinism | PK tiebreaker appended to every ordering |
| Default | `created_at desc` (unchanged behavior) |
| Page links | `Paginator` echoes order params into next/previous |
| Filter/paginate composition | `.order_by` between `.where` and `.limit/.offset`; count untouched |

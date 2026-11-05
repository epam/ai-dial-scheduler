# Phase 0 Research: Schedule Description & Name Filter

All Technical Context items were resolvable from the existing codebase and spec Assumptions; there were no open `NEEDS CLARIFICATION` markers. This document records the design decisions and the alternatives weighed.

## Decision 1 — Description storage: nullable `String` column

- **Decision**: Add `description` as a nullable `VARCHAR` column on the `schedules` table (no server default; NULL = absent).
- **Rationale**: The description is a single scalar string, not structured data, so a plain text column is the simplest fit (KISS/YAGNI). Nullable with no backfill keeps the migration reversible and makes pre-existing rows automatically "empty" per FR-015. Mirrors how other scalar columns (`display_name`, `service_id`) are modeled.
- **Alternatives considered**:
  - *JSONB / reuse metadata*: rejected — description is not a key/value tag and does not need containment indexing.
  - *NOT NULL with `''` server default*: rejected — forces a backfill and blurs the "absent vs. empty" distinction; nullable is cleaner and the response layer can normalize to `None`.

## Decision 2 — Maximum description length: 1000 characters, enforced in Pydantic

- **Decision**: Enforce `max_length=1000` on the request schema field (Pydantic v2 `Field(max_length=1000)`); measured in characters. Do not rely on a DB-level length cap as the primary guard.
- **Rationale**: Validating at the API boundary yields a clean 422 with a descriptive error before any DB round trip (FR-006, SC-002). 1000 characters comfortably fits operational notes while bounding abuse. Character-based counting satisfies the Unicode edge case in the spec.
- **Alternatives considered**:
  - *DB `VARCHAR(1000)` as sole guard*: rejected — surfaces as a 500/opaque driver error rather than a validated 422. A matching DB length may still be added defensively, but the schema validator is authoritative.
  - *Shorter cap (e.g., 255)*: rejected — too tight for multi-line operational notes; 1000 is a reasonable middle ground documented in the spec Assumptions.

## Decision 3 — Name filter matching: case-insensitive substring via `ILIKE`

- **Decision**: Filter `display_name` with PostgreSQL `ILIKE '%term%'`, escaping LIKE wildcards (`%`, `_`) in the user term so they are treated literally.
- **Rationale**: Case-insensitive substring is the conventional "search box" behavior (FR-010, spec Assumptions). `ILIKE` is the idiomatic PostgreSQL operator and is directly expressible in SQLAlchemy via `col.ilike(...)`. Escaping wildcards prevents a user's `%`/`_` from silently changing match semantics.
- **Alternatives considered**:
  - *`LOWER(col) LIKE LOWER(term)`*: rejected — `ILIKE` is clearer and equivalent on PostgreSQL.
  - *Exact / prefix match*: rejected — spec calls for substring search.
  - *Full-text search (`tsvector`)*: rejected — YAGNI for a name lookup; adds indexing complexity with no requirement for ranking or stemming.

## Decision 4 — Name filter transport: optional `name` query parameter via a dependency

- **Decision**: Expose the filter as an optional `?name=<term>` query parameter, parsed through a small FastAPI dependency (`parse_name_filter`) returning `str | None`, mirroring `parse_metadata_filter`. An empty/whitespace-only value is normalized to `None` (no filter), per the spec edge case.
- **Rationale**: Consistency with the established metadata-filter dependency pattern (Constitution V — reuse, no new abstraction style). Keeping normalization in the dependency keeps the endpoint and service signatures clean.
- **Alternatives considered**:
  - *Inline `Query(None)` parameter on the endpoint*: acceptable, but a dedicated dependency centralizes the empty→None normalization and matches the existing filter idiom.

## Decision 5 — Filter composition: AND at the SQL WHERE level

- **Decision**: Add the name `ILIKE` condition to the same `conditions` list the repository already builds for `created_by` and metadata containment, so all filters compose with AND and share the count/page statements (FR-013).
- **Rationale**: The repository `list` method already accumulates WHERE conditions; appending one more keeps pagination totals correct and composition automatic. No change to the pagination contract.
- **Alternatives considered**:
  - *Post-query in-memory filtering*: rejected — breaks pagination totals and is inefficient.

## Decision 6 — Response exposure: add `description` to detail and list schemas

- **Decision**: Add `description: str | None` to `ScheduleResponse` and `ScheduleListItem`; `_to_response` and the list mapping in the service populate it from the model.
- **Rationale**: FR-007 and FR-008 require the description on both surfaces. `ScheduleListItem` currently omits it, so the list row shape changes additively (a new optional field), which is backward compatible for consumers.
- **Alternatives considered**:
  - *Details only*: rejected — the spec explicitly requires the description on the list too.

## Decision 7 — Trigger on list rows: reuse the details `trigger` shape

- **Decision**: Add `trigger: dict[str, Any]` to `ScheduleListItem`, populated from `schedule.trigger` in the service's list mapping — the same value and shape already returned by `ScheduleResponse.trigger` on details. Keep the existing `trigger_type` field (FR-017).
- **Rationale**: The trigger JSON is already loaded on the ORM model and needs no extra query (the list already fetches full `Schedule` rows). Reusing the identical shape guarantees list/detail parity (FR-016, SC-006) at zero DB cost. `trigger_type` is retained for backward compatibility.
- **Alternatives considered**:
  - *Replace `trigger_type` with `trigger`*: rejected — removing an existing field is a breaking change; the spec requires the trigger *in addition to* the type.
  - *Derive a summarized/normalized trigger for the list*: rejected — YAGNI and would diverge from the details shape, violating the parity requirement.

## Summary of resolved constraints

| Item | Resolution |
|------|------------|
| Description length | 1000 characters, Pydantic `max_length` (422 on violation) |
| Empty description | NULL in DB, normalized to `None`/absent in responses |
| Name match | Case-insensitive substring `ILIKE`, wildcards escaped |
| Name param | Optional `?name=`, empty → no filter |
| Composition | AND with metadata filter + owner scope + pagination |
| Backward compat | Nullable column, no backfill; pre-existing rows return empty description |

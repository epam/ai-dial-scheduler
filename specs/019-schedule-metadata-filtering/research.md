# Phase 0 Research: Schedule Metadata & Filtering

All Technical Context unknowns are resolved below. No open `NEEDS CLARIFICATION` items remain.

## R1. ORM attribute name for a `metadata` column

**Decision**: Map the DB column literally named `metadata` to a non-reserved Python attribute `schedule_metadata` on the `Schedule` model, e.g. a `Field` whose underlying column name is `"metadata"`.

**Rationale**: SQLAlchemy's declarative base (and therefore SQLModel) reserves the class attribute `metadata` for the `MetaData` object — the project's `BaseSQLModel` even assigns `SQLModel.metadata = metadata`. Declaring a mapped attribute literally named `metadata` collides with this and raises at class-definition time. Naming the attribute `schedule_metadata` while pinning the column name to `"metadata"` preserves the requested DB column name and the API field name (`metadata`, set at the schema layer) without touching the reserved attribute.

**Alternatives considered**:
- Name both attribute and column `meta` — rejected: the user explicitly asked for a column named `metadata`, and the API contract should expose `metadata`.
- Use a SQLModel field alias only — rejected: the conflict is at the SQLAlchemy attribute level, not Pydantic serialization; an alias does not avoid the reserved-attribute clash.

## R2. JSONB filtering semantics — string-only, exact, AND-composed

**Decision**: Filter using the PostgreSQL JSONB **containment** operator `@>`, building a single JSON object from all supplied `metadata__<key>=<value>` pairs (all values are strings from the query string) and testing `schedules.metadata @> :filter::jsonb`.

**Rationale**:
- Containment is **type-aware**: `{"count": 5} @> {"count": "5"}` is **false** because the stored value is a number, not the string `"5"`. This satisfies FR-009 (only string-valued keys match) with zero custom type-checking logic.
- Containment naturally **AND-composes**: a single object `{"tenant":"hcscd","env":"prod"}` matches only rows containing both pairs, satisfying FR-008.
- Exact full-value match (no partial/substring), satisfying the spec assumptions.
- GIN-indexable (see R3), so filtering scales.

In SQLAlchemy this is expressed without raw SQL via the `JSONB` comparator: `schedules_table.c.metadata.op("@>")(cast(filter_dict, JSONB))` or the `.contains()` method on a JSONB column. The `cast(..., JSONB)` ensures the bound parameter is a JSONB literal.

**Alternatives considered**:
- `metadata ->> key = value` per key — rejected: `->>` coerces any scalar to text, so `{"count":5}` would match `metadata__count=5`, violating FR-009. Would require an extra `jsonb_typeof(metadata -> key) = 'string'` guard per key — more code, no index benefit.
- `metadata -> key = to_jsonb(value)` — rejected: more verbose, equivalent result to containment, and does not compose as cleanly across keys.

## R3. Indexing strategy

**Decision**: Add a GIN index on the `metadata` column using the default `jsonb_ops` operator class (`CREATE INDEX ... USING gin (metadata)`), created in the same Alembic migration that adds the column.

**Rationale**: The default `jsonb_ops` GIN index directly accelerates the `@>` containment operator used for filtering. `jsonb_path_ops` is smaller/faster for `@>` specifically but does not support key-existence queries; since only containment is needed, either works, but `jsonb_ops` is the conventional default and keeps the door open for future key/existence filters. Index creation is part of the migration so it ships atomically with the column.

**Alternatives considered**:
- No index — rejected: list filtering would do a sequential JSONB scan; cheap now but a latent scaling hazard, and the index is a one-line addition.
- Expression indexes per known key (e.g. on `metadata->>'tenant'`) — rejected: keys are arbitrary/user-defined (YAGNI); a single GIN index covers all containment filters.

## R4. Backward compatibility for existing rows

**Decision**: Define the column as `JSONB NOT NULL DEFAULT '{}'::jsonb` (server default), with the ORM field defaulting to an empty dict.

**Rationale**: A NOT NULL column with a server default backfills all pre-existing `schedules` rows with `{}` during the migration, so FR-014 (existing schedules treated as empty metadata, continue to function) holds with no data migration step. Empty metadata matches no non-trivial `metadata__<key>` filter, which is the correct behavior.

**Alternatives considered**:
- Nullable column — rejected: introduces `NULL` vs `{}` ambiguity in both storage and filtering, and complicates response serialization (the response should always return an object).

## R5. Parsing arbitrary `metadata__<key>` query parameters in FastAPI

**Decision**: Implement a FastAPI dependency that reads the raw `Request.query_params` (a multidict), selects every parameter whose name starts with the `metadata__` prefix, strips the prefix to obtain the key, and returns a `dict[str, str]` filter map. The key is everything after the first `metadata__` prefix (supports keys containing `__`). For repeated identical filter keys, the last value wins (standard single-value resolution, per spec assumption). Empty map ⇒ no filtering.

**Rationale**: FastAPI/Pydantic cannot declare a dynamic set of unknown query-parameter names as typed function arguments; reading `Request.query_params` is the idiomatic way to accept an open-ended key namespace. Keeping this in `src/api/dependencies/` isolates the HTTP-transport concern from `lib/`, consistent with the constitution's layer rules (`lib/` stays transport-agnostic). The dependency yields a plain dict that the service forwards to the repository.

**Alternatives considered**:
- A single `metadata` JSON-string query param (e.g. `?metadata={"tenant":"hcscd"}`) — rejected: the spec's contract is explicitly `metadata__tenant=hcscd`.
- Catch-all via `**kwargs` in the route — unsupported by FastAPI's signature-based parameter binding.

## R6. Where metadata appears in API responses

**Decision**: Add `metadata` to `ScheduleResponse` (returned by create, update, and details). Do **not** add it to `ScheduleListItem` in this scope.

**Rationale**: Matches FR-006 (details endpoint shows metadata) and the spec assumption that list row shape is unchanged. Create/update already return `ScheduleResponse`, so they include metadata "for free", aiding SC-001 round-trip verification. Adding metadata to list rows is deferred (YAGNI) until a concrete need arises.

**Alternatives considered**:
- Add metadata to list rows immediately — rejected as out of declared scope; easy to add later without breaking the contract.

# Phase 1 Data Model: Schedule Metadata & Filtering

## Entity: Schedule (modified)

Existing table `schedules`. This feature adds one column.

| Field (ORM attr) | DB column | Type | Null | Default | Notes |
|------------------|-----------|------|------|---------|-------|
| `id` | `id` | VARCHAR(191) | No | — | PK = APScheduler job id (unchanged) |
| `display_name` | `display_name` | VARCHAR | No | — | unchanged |
| `created_by` | `created_by` | VARCHAR | No | — | owner identity (unchanged) |
| `trigger` | `trigger` | JSONB | No | — | unchanged |
| `kwargs` | `kwargs` | JSONB | No | — | call properties (unchanged) |
| **`schedule_metadata`** | **`metadata`** | **JSONB** | **No** | **`'{}'::jsonb`** | **NEW** — string→JSON map of business tags |
| `created_at` | `created_at` | TIMESTAMPTZ | No | `now()` | from `BaseSQLModel` (unchanged) |
| `updated_at` | `updated_at` | TIMESTAMPTZ | No | `now()` / `onupdate` | from `BaseSQLModel` (unchanged) |

**ORM attribute name**: `schedule_metadata` (the literal name `metadata` is reserved by SQLAlchemy declarative — see research R1). The column name in the database is exactly `metadata`.

### Indexes (new)

- `IX_schedules_metadata` — GIN index on `metadata` (`USING gin (metadata)`, default `jsonb_ops`) to accelerate `@>` containment filtering (research R3). Index name follows the project naming convention (`ix` template `IX_%(column_0_label)s`); the migration may set an explicit name for clarity.

### Validation / invariants

- `metadata` is always a JSON object (map). Top-level value must be an object, never a scalar or array.
- Keys are strings (JSON object keys always are). Values may be any JSON type; only **string** values are eligible for filtering.
- On create without metadata → stored as `{}`. On update → full replacement (no per-key merge), consistent with existing update semantics.
- Pre-existing rows backfill to `{}` via the server default (FR-014).

## Schema: ScheduleRequest (modified — request body)

Add field:

| Field | Type | Required | Default | Validation |
|-------|------|----------|---------|------------|
| `metadata` | `dict[str, Any]` | No | `{}` (empty) | Must be a JSON object; absent ⇒ empty object (FR-005). |

`metadata` is a valid attribute name on a plain Pydantic v2 `BaseModel` (no clash with the `model_*` namespace).

## Schema: ScheduleResponse (modified — create/update/details response)

Add field:

| Field | Type | Notes |
|-------|------|-------|
| `metadata` | `dict[str, Any]` | Full stored metadata object, echoed verbatim (FR-006). Always an object (never null). |

Mapping: `ScheduleService._to_response` reads `schedule.schedule_metadata` into `ScheduleResponse.metadata`.

## Schema: ScheduleListItem (unchanged)

No metadata field added in this scope (research R6 / spec assumption).

## Filter contract: metadata query parameters

**Input form**: zero or more query parameters `metadata__<key>=<value>` on `GET /schedules/`.

**Parsed shape**: `dict[str, str]` — `{<key>: <value>, ...}`, key = substring after the first `metadata__` prefix; value = raw query string value (string). Empty when no such params present.

**Applied semantics** (repository layer):
- Build a JSON object from the parsed map and apply `schedules.metadata @> :filter::jsonb`.
- AND across all keys (single containment object) — FR-008.
- String-only, exact, case-sensitive match — FR-009 + assumptions (containment is type-aware; numeric/boolean/object/array stored values never match a string filter).
- Combined with the existing `created_by == user_id` owner scope (FR-011) and pagination limit/offset (FR-012).
- Empty filter map ⇒ identical to current unfiltered behavior (FR-010).
- Filter key absent from all owned rows ⇒ empty page, no error (FR-013).

## Repository interface change

`ScheduleRepository.list(...)` gains a metadata filter parameter (keyword-only) carrying the parsed `dict[str, str]`. When the dict is empty, the query is unchanged from today. When non-empty, both the count query and the page query gain the containment predicate so `count` reflects the filtered total (consistent pagination).

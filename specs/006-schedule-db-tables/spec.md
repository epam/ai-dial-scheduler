# Feature Specification: Schedule & Run Persistence Tables

**Feature Branch**: `006-schedule-db-tables`

**Created**: 2026-06-10

**Status**: Draft

**Input**: User description: "Analyze APScheduler 3.11.2 and documentation/db_tables.md, Create tables that will extend APScheduler table with schedules data that can be queried, and add table with runs. Create table models, and migrations. Take into account that schedules table query should be able to join apscheduler_jobs and bring actual next_run_time. Only tables and migration in scope of this task, no apis. Migrations are only alembic generated, no manual changes in migrations"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Query schedules with live next-run time (Priority: P1)

The schedule data consumer (the future API layer and operators) needs to read rich,
queryable schedule metadata — display name, owner, trigger definition, and call
arguments — together with the *actual* next execution time that the scheduling engine
currently holds. The scheduling engine keeps the authoritative next-run time in its own
job store table (`apscheduler_jobs`), while the human-meaningful metadata lives in a
separate, queryable table. A single query must be able to combine both so that a caller
gets one consistent view per schedule.

**Why this priority**: Without a queryable schedule table that can be joined to the
engine's job store, there is no way to list, filter, or sort schedules by business
attributes while still reflecting the engine's authoritative next-run time. This is the
foundational capability the entire scheduler product is built on; every later feature
(listing, filtering, dashboards, APIs) depends on it.

**Independent Test**: Insert a schedule metadata row whose identifier matches an existing
engine job-store row, then run a single query that joins the two and confirm it returns
the schedule's metadata plus the engine's current next-run time. Delivers value because it
proves the core "queryable schedule + live next-run time" view works end to end.

**Acceptance Scenarios**:

1. **Given** a schedule metadata row and a matching engine job-store row with a populated
   next-run time, **When** the schedules view is queried by joining on the shared
   identifier, **Then** the result contains the schedule's display name, owner, trigger,
   call arguments, and the engine's current next-run time.
2. **Given** a schedule metadata row whose matching engine job is paused (no next-run
   time), **When** the schedules view is queried, **Then** the schedule metadata is still
   returned and the next-run time is reported as empty rather than causing the row to be
   dropped.
3. **Given** a schedule metadata row with no corresponding engine job-store row, **When**
   the schedules view is queried, **Then** the schedule metadata is still returned with an
   empty next-run time (the join does not discard the metadata row).

---

### User Story 2 - Persist a complete history of schedule runs (Priority: P2)

Every time a schedule fires, the system must record an individual run entry capturing
when it ran, when it finished, the outcome (success, error, in-progress, or missed), and
the produced result. The run entry must also retain a point-in-time snapshot of the
schedule definition (display name, trigger, call arguments) as it existed when the run
occurred, so the run history remains accurate even if the schedule is later edited.

**Why this priority**: Run history is required for observability and auditing of the
scheduler, but it builds on top of the schedule table existing first (P1). It is the
second pillar of the data model and is mandated by the project's observability
requirement that all task runs — successful and failed — be persisted.

**Independent Test**: Create a schedule, then insert run records against it with each of
the possible statuses, including one in-progress run with no end time, and confirm all
runs can be read back with their status, timing, result, and definition snapshot intact.

**Acceptance Scenarios**:

1. **Given** an existing schedule, **When** a run record is created with status
   "in_progress" and no end time, **Then** the run is persisted and readable with an empty
   end time.
2. **Given** an in-progress run, **When** the run completes and is updated to status
   "success" (or "error") with an end time and result, **Then** the updated values are
   persisted.
3. **Given** a schedule whose definition is later changed, **When** earlier run records
   are read, **Then** each run still reports the schedule definition snapshot captured at
   the time that run occurred, unaffected by the later change.

---

### User Story 3 - Protect run history integrity against schedule deletion (Priority: P3)

Schedule run history must not be silently lost. When a schedule still has recorded runs,
the system must prevent the schedule from being deleted, protecting the audit trail.

**Why this priority**: This is an integrity guardrail that hardens the model created in
P1 and P2. It is valuable but not required to demonstrate the core read/write capability,
so it is prioritized last.

**Independent Test**: Create a schedule, attach at least one run to it, attempt to delete
the schedule, and confirm the deletion is rejected while the schedule and its runs remain
intact.

**Acceptance Scenarios**:

1. **Given** a schedule that has one or more associated runs, **When** deletion of the
   schedule is attempted, **Then** the deletion is rejected and both the schedule and its
   runs are preserved.
2. **Given** a schedule that has no associated runs, **When** deletion of the schedule is
   attempted, **Then** the deletion succeeds.

---

### Edge Cases

- **Paused job (empty next-run time)**: The engine sets no next-run time for a paused job;
  the schedule must still appear in the joined view with an empty next-run time.
- **Metadata without engine job / engine job without metadata**: The shared identifier may
  exist on only one side during creation/teardown windows; the joined schedules view must
  not drop schedule metadata rows when the engine row is absent.
- **Identifier length / type mismatch**: The schedule identifier must be storage-compatible
  with the engine job-store identifier so the join works without truncation or casting.
- **Orphaned in-progress runs**: A run may stay "in_progress" indefinitely (e.g., worker
  crash); the model must allow an empty end time and not assume completion.
- **Concurrent run inserts for the same schedule**: Multiple run rows can reference the same
  schedule; the run identifier must be globally unique and independent of the schedule.
- **Deleting a schedule with runs**: Must be blocked (see User Story 3) rather than
  cascading or orphaning runs.

## Requirements *(mandatory)*

### Functional Requirements

#### Schedules table

- **FR-001**: The system MUST provide a persistent `schedules` table holding queryable
  business metadata for each schedule, separate from the scheduling engine's own job-store
  table.
- **FR-002**: Each schedule record MUST have a primary identifier that is logically equal
  to the corresponding scheduling-engine job identifier, so the two can be joined. This is
  a logical relationship only — no database-enforced foreign-key constraint is created
  against the engine's job-store table (it is owned and managed by the engine, not by this
  project's migrations).
- **FR-003**: The schedule identifier MUST be storage-compatible (same type and at least
  the same maximum length) with the scheduling engine's job identifier so joins succeed
  without truncation or type casting.
- **FR-004**: Each schedule record MUST persist: a creation timestamp, a last-updated
  timestamp, a human-readable display name, the identity of the creator/owner, the trigger
  definition (structured data), and the call arguments (structured data).
- **FR-005**: The system MUST allow retrieving schedule metadata joined to the engine's
  job store such that the result includes the engine's authoritative current next-run time
  for each schedule.
- **FR-006**: The joined retrieval MUST preserve every schedule metadata row even when the
  matching engine job-store row is absent or has no next-run time, reporting an empty
  next-run time in those cases (i.e., metadata rows are never dropped by the join).

#### Schedule runs table

- **FR-007**: The system MUST provide a persistent `schedule_runs` table that records one
  entry per schedule execution attempt, covering both successful and failed/missed runs.
- **FR-008**: Each run record MUST have a globally unique identifier that is independent of
  the schedule identifier (so many runs can reference one schedule).
- **FR-009**: Each run record MUST persist: a creation timestamp, a last-updated timestamp,
  an optional end timestamp (empty while the run has not finished), an execution status, a
  result payload (structured data), the referenced schedule identifier, and a snapshot of
  the schedule definition at run time.
- **FR-010**: The run status MUST be constrained to a known set of values: `success`,
  `error`, `in_progress`, and `missed`.
- **FR-011**: The end timestamp MUST be optional so that in-progress runs (not yet
  finished) can be represented.
- **FR-012**: Each run record MUST store a self-contained snapshot of the schedule's
  display name, trigger definition, and call arguments as they were at run time, so the run
  history stays accurate independently of later schedule edits.

#### Referential integrity

- **FR-013**: Each run record MUST reference an existing schedule via a database-enforced
  foreign key on the schedule identifier.
- **FR-014**: The system MUST prevent deletion of a schedule while any run still references
  it (delete is restricted/protected, not cascaded), preserving the run history.

#### Models & migrations

- **FR-015**: The system MUST express both tables as ORM models consistent with the
  project's existing model conventions (shared base providing managed created/updated
  timestamps, project naming conventions, structured/JSON columns, timezone-aware
  timestamps).
- **FR-016**: The system MUST provide database migrations that create both tables and their
  constraints, generated solely by the migration tool's autogeneration from the models —
  with no manual edits to the generated migration scripts.
- **FR-017**: The migration autogeneration MUST NOT attempt to create, drop, or alter the
  scheduling engine's job-store table; that table remains excluded from this project's
  managed schema.
- **FR-018**: Applying the generated migration to an empty database MUST create both tables
  with all specified columns, the primary keys, the run-to-schedule foreign key with delete
  protection, and the status value constraint; the migration MUST also be reversible
  (downgrade removes what upgrade created).

### Key Entities *(include if feature involves data)*

- **Schedule**: Queryable business metadata for a single schedule. Identified by a value
  equal to the scheduling engine's job identifier (logical link, no enforced FK to the
  engine table). Attributes: display name, creator/owner, trigger definition, call
  arguments, created/updated timestamps. Has many Runs.
- **Schedule Run**: A single recorded execution attempt of a Schedule. Identified by its own
  globally unique identifier. Attributes: status (success/error/in_progress/missed),
  optional end time, result payload, schedule-definition snapshot, created/updated
  timestamps. References exactly one Schedule (deletion of that Schedule is blocked while
  runs exist).
- **Scheduling-engine job (external)**: The engine's own job-store row that holds the
  authoritative next-run time and serialized job state. Owned and managed by the scheduling
  engine, not by this project's migrations. Joined to Schedule on the shared identifier to
  surface the live next-run time. Out of scope to create or modify.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A single query returns, for any given schedule, its full business metadata
  plus the scheduling engine's current next-run time, with 100% of schedule metadata rows
  preserved even when the matching engine row is missing or has no next-run time.
- **SC-002**: Run records can be stored and read back for every supported status value
  (success, error, in_progress, missed), including in-progress runs that have no end time,
  with 100% fidelity of stored fields (timing, status, result, snapshot).
- **SC-003**: A schedule definition snapshot stored on a run remains unchanged after the
  parent schedule's metadata is subsequently edited (run history is immutable with respect
  to later schedule changes).
- **SC-004**: 100% of attempts to delete a schedule that still has associated runs are
  rejected, with no run records lost.
- **SC-005**: The generated migration applies cleanly to an empty database and fully
  reverses, with no manual edits to the migration script and without creating, altering, or
  dropping the scheduling engine's job-store table.

## Assumptions

- **A-001 (Engine job store)**: The scheduling engine is APScheduler 3.11.2 using its
  SQLAlchemy job store, whose table is `apscheduler_jobs` with an `id` column
  (`VARCHAR(191)`), a `next_run_time` column (float epoch seconds, empty when the job is
  paused), and a serialized `job_state` column. This table already exists / is created by
  the engine and is excluded from this project's Alembic autogenerate (it is in the
  unmanaged-tables set).
- **A-002 (Identifier type)**: The `schedules.id` primary key is a string sized to match
  APScheduler's `VARCHAR(191)` so the join to `apscheduler_jobs.id` requires no casting.
- **A-003 (Run identifier)**: `schedule_runs.id` is a UUID, generated independently of the
  schedule identifier.
- **A-004 (Structured columns)**: `trigger`, `kwargs`, `result`, and `schedule_snapshot`
  are stored as JSON columns (PostgreSQL `JSONB`).
- **A-005 (Status representation)**: Run status is modeled as a fixed-value enumeration in
  code; whether it is enforced in the database via a native enum type or an equivalent
  value constraint is an implementation detail to be settled in planning, with the
  constraint that the choice must remain compatible with autogenerated, manually-unedited
  migrations.
- **A-006 (Timestamps)**: `created_at` / `updated_at` are timezone-aware and managed by the
  shared model base (server-side defaults / on-update), consistent with existing models;
  `end_time` is a nullable timezone-aware timestamp.
- **A-007 (Delete protection)**: "On delete protect" is implemented as a `RESTRICT`
  (or equivalent no-action) foreign-key rule from `schedule_runs.schedule_id` to
  `schedules.id`.
- **A-008 (Scope)**: This feature delivers only the ORM models and the autogenerated
  migration. No API endpoints, services, repositories, or worker wiring are in scope;
  those are separate, later features that will consume these tables.
- **A-009 (Database)**: Target database is PostgreSQL, accessed asynchronously, consistent
  with the project's existing configuration.

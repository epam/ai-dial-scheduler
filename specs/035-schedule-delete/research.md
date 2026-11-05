# Phase 0 Research: Support Schedules Delete

All items below were resolved against the current codebase; no `NEEDS CLARIFICATION` markers remain
in the Technical Context.

---

## D1 — Order of store mutations: unregister the job before writing to the database

**Decision**: `DELETE` removes the APScheduler job **first**, then hard-deletes or soft-marks the row.
`JobLookupError` from `remove_job` is swallowed as success; any other scheduler exception aborts the
operation with a new `JobRemovalError` → HTTP 502, leaving the row untouched.

**Rationale**: A schedule lives in two stores — the engine-owned `apscheduler_jobs` table and the
`schedules` table — and there is no shared transaction. FR-017 permits an error to be returned as
long as a retried delete can finish the job. That splits the two orderings decisively:

| Order | Failure after step 1 | Retryable? |
|-------|----------------------|------------|
| **Unregister → persist** | Row presented as live, but the job is gone so it can never fire. | **Yes** — the retry re-reads the row, finds `is_deleted = false`, hits `JobLookupError` (treated as success), and completes the persist. |
| Persist → unregister | Row marked deleted, but the job is still registered and **keeps firing**. | **No** — the FR-015 "already deleted" guard rejects the retry, so the still-firing job can never be cleaned up through the API. |

The second ordering converts a transient failure into a permanent contract violation (a schedule
presented as deleted that still executes, banned by SC-006). Swallowing `JobLookupError` also
directly implements the spec's "no corresponding entry in the scheduling engine" edge case and makes
the retry idempotent.

**Alternatives considered**:
- *Persist first* — rejected above.
- *Two-phase with compensation* (re-register the job if the persist fails, mirroring `create`'s
  rollback): rejected. Re-registering a job the user asked to delete can resurrect execution, and the
  compensation itself can fail — `update` already documents that residual risk. Leaving the job
  removed is the safe direction of failure for a delete.
- *Wrapping both stores in one transaction*: not available; APScheduler's `SQLAlchemyJobStore` runs on
  its own synchronous engine (`src/lib/db/jobstore.py`), separate from the async request session.

---

## D2 — Branch selection: explicit run count, with the FK as the race safety net

**Decision**: Count runs via a new `ScheduleRunRepository.count_for_schedule(schedule_id)`. Zero →
hard delete; non-zero → soft delete. If the hard delete raises `IntegrityError` (a run was inserted
after the count), roll back and fall through to the soft-delete branch.

**Rationale**: `ScheduleRun.__table_args__` already declares
`ForeignKeyConstraint(["schedule_id"], ["schedules.id"], ondelete="RESTRICT")`, so the database
refuses to delete a schedule that has runs regardless of what the application believes. The count is
therefore the *fast, legible, loggable* path (FR-005 frames the decision as "whether any run record
exists"; FR-018 wants the chosen branch logged), and the FK is the *correctness* guarantee. The
fallback turns the spec's documented race into the better of its two permitted outcomes —
soft-deleted with the new run preserved, rather than a failed request.

Any run status counts, including `in_progress`: the count has no status predicate. This is what makes
the "execution already in progress" edge case resolve correctly on its own — an in-flight run means a
row exists, so the schedule takes the soft-delete branch and the running execution can still write
its outcome.

**Implementation note added during build** — the fallback needs an explicit rollback. PostgreSQL
aborts the entire transaction when a statement fails, so after the FK-blocked `DELETE` the session
cannot run the fallback `UPDATE`: it fails with *"current transaction is aborted, commands ignored
until end of transaction block"*. `delete_owned` therefore catches `IntegrityError`, calls
`session.rollback()`, and re-raises, leaving the session usable for `mark_deleted`.

This was **not** caught by the mock-based service unit test (which stubs the repository and so never
exercises a real transaction) and only surfaced in an integration test that drives the real service
against PostgreSQL with the run count forced to 0 while a run genuinely exists
(`tests/integration/test_schedule_delete.py::TestRaceFallbackOnRealSession`). A unit-level
regression guard asserting the rollback now exists as well. Worth remembering: any
"catch the database error and continue on the same session" pattern in this codebase needs the
rollback, and needs a real-database test to prove it.

**Alternatives considered**:
- *No count; always attempt the hard delete and catch `IntegrityError`*: fewer queries and one less
  service dependency, but makes the primary branch selection exception-driven — harder to unit test
  with mocks, and it logs a database error on the entirely normal "schedule has history" path.
- *`EXISTS`/`LIMIT 1` instead of `COUNT(*)`*: marginally cheaper, but the count is a useful log field
  (FR-018) and the query is indexed by `IX_schedule_runs_schedule_id`. `count_for_schedule` also
  removes the duplicated count statement currently inlined in `list_for_schedule`.
- *Changing the FK to `ON DELETE CASCADE`*: rejected outright — it would destroy audit history, which
  FR-016 forbids and which is the entire reason the soft-delete branch exists.

---

## D3 — Concurrency: conditional writes decided by `rowcount`

**Decision**: Both persist paths are single conditional statements, and their `rowcount` decides the
outcome:
- `mark_deleted`: `UPDATE schedules SET is_deleted = true WHERE id = :id AND created_by = :owner AND is_deleted = false`
- `delete_owned`: `DELETE FROM schedules WHERE id = :id AND created_by = :owner AND is_deleted = false`

`rowcount == 0` means another request won the race (or the row is not owned) → `ScheduleAlreadyDeletedError`.

**Rationale**: The service already reads the schedule to check ownership and to pick the branch, so a
naive `if schedule.is_deleted: raise` guard is a read-then-write race — two concurrent deletes both
pass it. Folding `is_deleted = false` into the WHERE clause makes the database the arbiter, so
exactly one request succeeds without any explicit locking, satisfying the spec's concurrent-delete
edge case. The early read-based guard is still kept, because it turns the common case into a clean
409 without touching the scheduler.

SQLAlchemy Core `update()` honours the column-level `onupdate=func.now()` declared on
`BaseSQLModel.updated_at`, so a soft delete refreshes `updated_at` without extra handling.

**Alternatives considered**: `SELECT ... FOR UPDATE` around the read — correct but heavier, and it
holds a row lock across the scheduler call (which runs in a worker thread and can be slow). Rejected.

---

## D4 — `is_deleted` immutability relies on the existing schema boundary

**Decision**: Do not add `is_deleted` to `ScheduleRequest`. Do not add `model_config` /
`extra="forbid"`. Add it only to `ScheduleResponse` and `ScheduleListItem`.

**Rationale**: `src/lib/schemas/schedule.py` declares no `model_config`, so every request schema uses
Pydantic v2's default `extra="ignore"`. A client sending `{"is_deleted": true}` to POST or PUT has the
field silently dropped before it reaches the service — exactly the behaviour FR-009 specifies
("MUST ignore any deletion-state value supplied in a create or update request body"). Nothing in
`ScheduleRepository.update` touches the column either, so a full-replacement PUT cannot clear a soft
delete. This needs zero code; it needs a **test** asserting the guarantee, so a future
`extra="forbid"` or a stray field addition cannot silently regress it.

**Alternatives considered**: `extra="forbid"` on `ScheduleRequest` — would return 422 instead of
ignoring, contradicting FR-009's wording and breaking any existing client that sends extra fields.
Rejected as both out of scope and a breaking change.

---

## D5 — List filter: a plain `is_deleted` boolean query parameter, default `false`

**Decision**: `GET /v1/schedules?is_deleted=<bool>`, default `false`. Threaded as a `bool` through
`ScheduleService.list` into `ScheduleRepository.list`, where it becomes an equality predicate applied
to **both** the count statement and the page statement. When non-default, it is appended to
`paginator.extra_query` so `next`/`previous` links preserve it.

**Rationale**: This implements the spec's documented assumption (default view shows live schedules;
the filter is two-valued). A bare `bool` needs no dependency module — unlike `ordering.py` and
`pagination.py`, which group several parameters into a value object — and FastAPI validates it,
rejecting `?is_deleted=maybe` with 422 for free.

Two details are load-bearing:
- The predicate must be added to the **count** statement, not just the page statement, or `count`
  would report live+deleted totals and break pagination (FR-012).
- The links must carry the parameter. `ScheduleService.list` currently *assigns*
  `paginator.extra_query = ordering.to_query()`; appending is required, otherwise page 2 of a
  deleted-only list silently returns live schedules. (Name and metadata filters have this same
  pre-existing gap; fixing them is out of scope, but `is_deleted` must not join them, because
  flipping between live and deleted mid-traversal is a correctness bug rather than a lost
  convenience.)

**Alternatives considered**:
- *Three-valued `live | deleted | all` enum*: no stated use case, and the spec explicitly frames the
  list as filtered by the field. YAGNI.
- *Excluding soft-deleted rows unconditionally, with no parameter*: makes the required `is_deleted`
  list field permanently `false` and leaves soft-deleted schedules unreachable via the list.
  Contradicts the spec.

---

## D6 — No new index on `is_deleted`

**Decision**: Add the column with no index.

**Rationale**: The list query already filters on `created_by`, which itself is **not** indexed today;
adding an index for a two-valued boolean while the selective column has none would be misdirected
tuning. Boolean columns are poor standalone index candidates (very low selectivity), and Postgres will
keep using a sequential or existing-index scan either way at the expected per-user row counts. This
matches the precedent set by the `description` column migration, which deliberately added no index.

**Alternatives considered**: composite `(created_by, is_deleted)` index, or a partial index
`WHERE is_deleted = false`. Both are reasonable *if* schedule volume grows, but neither is justified
now; note them as future work if list latency is ever measured as a problem.

---

## D7 — HTTP 409 for operations on an already-deleted schedule

**Decision**: New `ScheduleAlreadyDeletedError` → **409 Conflict** on `DELETE`, `PUT`,
`POST /pause`, and `POST /resume`. `GET /{schedule_id}` continues to return the schedule with
`is_deleted: true`.

**Rationale**: This refines the spec's assumption, which suggested reusing the not-found error style.
404 is inconsistent with the rest of the design: `GET` deliberately still returns a soft-deleted
schedule (so the owner can see the flag and reach the surviving run history), so claiming the same
resource does not exist on the next request would be self-contradictory. 409 states the actual
condition — the resource exists but is in a state that forbids the operation — and lets clients
distinguish "gone" from "deleted", which matters because the two delete branches produce different
observable outcomes (404 for a hard-deleted schedule, 409 for a re-delete of a soft-deleted one).
There is no information-disclosure concern: every route is already owner-scoped, so a foreign
schedule still yields 404 and stays indistinguishable from a nonexistent one.

**Alternatives considered**: 404 everywhere (spec's suggestion) — simpler but conflicts with `GET`, as
above. 410 Gone — semantically attractive for hard deletes, but it is defined for permanently removed
*URIs* and would misdescribe a soft-deleted resource that still responds 200 on `GET`.

---

## D8 — Worker pre-flight guard closes the cross-process window

**Decision**: At the top of `_execute_scheduled_job_inner`, before creating the `in_progress` run
row, load the schedule by primary key; if it is missing or `is_deleted`, log a skip event and return
without creating a run record.

**Rationale**: The API and the worker are separate processes sharing one job store. `remove_job`
called from the API deletes the `apscheduler_jobs` row, but the worker's scheduler may already have
fetched and submitted a due job. Without the guard, that in-flight submission would create a run
record for a deleted schedule — violating FR-006 ("no further executions and no further run
records"). For a hard-deleted schedule the FK would reject the insert anyway, but only as a logged
error rather than a clean skip; for a soft-deleted schedule nothing would stop it at all.

Cost is one indexed primary-key lookup per run, on a path that already opens a session and performs
several round trips (`create_in_progress`, then `get_by_id` inside `_resolve_credentials`). The guard
must run *before* `create_in_progress`, otherwise the run record it is meant to prevent already exists.

**Alternatives considered**:
- *Guard inside `_resolve_credentials`* (which already loads the schedule): the run row is created
  before that point, so a deleted schedule would still gain a record. Fails FR-006.
- *No guard, rely on `remove_job` alone*: leaves the documented cross-process window open, and leaves
  the soft-delete case entirely unguarded.
- *Also filter `is_deleted` inside `get_by_id`*: rejected — `get_by_id` is a generic accessor, and
  hiding rows inside it would silently change unrelated callers.

---

## D9 — Migration shape: `NOT NULL` with a retained server default

**Decision**: One Alembic revision (`down_revision = "d4e2b3c5f6a7"`) adding
`sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false"))`. The server
default is kept permanently, not dropped after backfill. `downgrade()` drops the column.

**Rationale**: Adding a `NOT NULL` column with a server default backfills every existing row in the
same statement — Postgres 11+ does this without a table rewrite, so no separate data migration and no
multi-step nullable→backfill→alter dance is needed. Existing schedules become `is_deleted = false`,
which is correct: nothing has been deleted yet (FR-007). Retaining the default keeps direct SQL
inserts and any future bulk load valid, matching how `schedules.metadata` retains its
`'{}'::jsonb` default. The revision id and the `YYYYMMDD_HHMM_<revid>_<slug>.py` filename follow the
existing convention in `migrations/versions/`.

**Alternatives considered**: nullable column treating `NULL` as "not deleted" — pushes three-valued
logic into every query predicate for no benefit. Rejected.

---

## Cross-cutting finding: run history needs no code change

`ScheduleRunService.list_runs` and `get_run` resolve the schedule through
`ScheduleRepository.get(schedule_id, created_by)`, which filters on `id` and `created_by` only. Since
the design deliberately leaves `get` unfiltered by `is_deleted`, FR-016 (run history of a
soft-deleted schedule stays retrievable) is satisfied with **zero changes** to the runs layer. This
is a behaviour worth pinning with an integration test, since a later "helpful" addition of an
`is_deleted` filter to `get` would break it silently.

## Cross-cutting finding: the new route is auto-exposed over MCP

`src/api/mcp/route_maps.py` maps every route tagged `schedules` to `MCPType.TOOL`, and the server is
built from the generated OpenAPI spec. The new `DELETE` route therefore becomes an MCP **tool**
automatically, with no change to `route_maps.py` — meaning an MCP client (i.e. a model) gains a
destructive schedule-deletion tool as a side effect of this feature. That is consistent with how
create/update/pause/resume are already exposed, and the operation is owner-scoped and irreversible-
by-design rather than unbounded. `tests/unit/api/mcp/test_mcp_tools.py` asserts the exposed tool set
and must be updated. Flagged here so the exposure is a recorded decision rather than an accident; if
it is unwanted, the fix is an explicit `RouteMap` excluding `DELETE` on the schedules tag.

## Cross-cutting finding: route registration order is fragile

`src/api/v1/endpoints/schedules.py` appends OpenAPI examples via
`router.routes[-1].description += ...` immediately after the POST and PUT decorators. Any new route
inserted between a decorator and its following `routes[-1]` line would receive the examples instead.
The `DELETE` handler must therefore be appended at the end of the file (after `resume_schedule`).

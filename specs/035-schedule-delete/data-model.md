# Phase 1 Data Model: Support Schedules Delete

## 1. Schema change

### `schedules.is_deleted`

| Property | Value |
|----------|-------|
| Column | `is_deleted` |
| Type | `BOOLEAN` |
| Nullable | `NOT NULL` |
| Server default | `false` (retained permanently — see research.md D9) |
| Python default | `False` |
| Index | none (research.md D6) |
| Backfill | Implicit: existing rows become `false` in the `ADD COLUMN` statement |

Model addition in `src/lib/models/schedule.py`:

```text
is_deleted: bool  →  Field(default=False, sa_column_kwargs={"nullable": False, "server_default": text("false")})
```

Docstring addition: *Soft-delete marker. Set to true only by the delete operation when run history
exists; never cleared — deletion is irreversible (FR-014).*

### Unchanged but load-bearing

`schedule_runs.__table_args__` already declares
`ForeignKeyConstraint(["schedule_id"], ["schedules.id"], ondelete="RESTRICT")`. This is the guarantee
that run history can never be destroyed by a schedule delete, and the mechanism behind the
race-fallback in the service. **It must not be changed to CASCADE.**

## 2. Entity states and transitions

A schedule occupies exactly one of three states:

| State | `schedules` row | `is_deleted` | `apscheduler_jobs` row | Visible in default list | `GET /{id}` | Can fire |
|-------|-----------------|--------------|------------------------|-------------------------|-------------|----------|
| **Live** | present | `false` | present (`next_run_time` set, or `NULL` when paused) | yes | 200 | yes (unless paused) |
| **Soft-deleted** | present | `true` | absent | only with `is_deleted=true` | 200, `is_deleted: true`, `next_run_time: null` | no |
| **Removed** | absent | — | absent | no (under any filter) | 404 | no |

Transitions:

```text
                        DELETE, run_count == 0
        Live ─────────────────────────────────────────▶ Removed   (terminal)
          │
          │             DELETE, run_count > 0
          └─────────────────────────────────────────▶ Soft-deleted (terminal)

   Soft-deleted ──▶ (no transition exists — no restore, no re-delete, no update/pause/resume)
```

Both end states are terminal. No code path sets `is_deleted` back to `false`, and no path re-creates a
removed row under the same id (ids are freshly generated `uuid4` on create).

A paused schedule is **not** a separate state for this feature: pausing is represented only by
`apscheduler_jobs.next_run_time IS NULL`, so a paused schedule deletes identically to an active one.

**Accepted residual race.** The worker's pre-flight guard is a check, not a lock: a soft delete that
commits between the guard's read and `create_in_progress` still yields one run record for a deleted
schedule, which is strictly an FR-006 miss. This collapses into the race the spec already tolerates —
the API's hard→soft fallback exists precisely to accept a run that appears at delete time, and the
outcome (schedule soft-deleted, the late run preserved) is one of the two the spec permits. Closing
it would require locking on the run-creation path, which is not justified for a window measured in
milliseconds on a path that produces no further runs afterwards.

## 3. Layer contracts

### Repository — `ScheduleRepository` (`src/lib/repositories/schedule_repository.py`)

| Method | Signature | Semantics |
|--------|-----------|-----------|
| `delete_owned` *(new)* | `(schedule_id: str, created_by: str) -> bool` | Single conditional `DELETE ... WHERE id AND created_by AND is_deleted = false`; returns `rowcount == 1`. Raises `IntegrityError` when the FK blocks the delete (runs exist). |
| `mark_deleted` *(new)* | `(schedule_id: str, created_by: str) -> bool` | Single conditional `UPDATE ... SET is_deleted = true WHERE id AND created_by AND is_deleted = false`; returns `rowcount == 1`. `updated_at` refreshes via the column's `onupdate`. |
| `list` *(changed)* | `(..., *, is_deleted: bool = False, ...)` | Adds `is_deleted_col == is_deleted` to the shared `conditions` list, which feeds **both** the count statement and the page statement. |
| `delete` *(unchanged)* | `(schedule_id: str) -> None` | Kept as-is: unscoped compensating transaction used only by `create`'s rollback. Not used by the delete endpoint. |
| `get`, `get_by_id` *(unchanged)* | — | Deliberately do **not** filter `is_deleted`, so soft-deleted schedules remain retrievable and their run history reachable (FR-016). |

### Repository — `ScheduleRunRepository` (`src/lib/repositories/schedule_run_repository.py`)

| Method | Signature | Semantics |
|--------|-----------|-----------|
| `count_for_schedule` *(new)* | `(schedule_id: str) -> int` | `SELECT count(*) WHERE schedule_id = :id`. **No status predicate** — `in_progress`, `success`, `error`, and `missed` all count. |
| `list_for_schedule` *(refactor)* | unchanged signature | Its inlined count statement is replaced by a call to `count_for_schedule` (DRY; identical predicate). |

### Service — `ScheduleService` (`src/lib/services/schedule_service.py`)

Constructor grows one argument:

```text
__init__(self, repository: ScheduleRepository, run_repository: ScheduleRunRepository,
         scheduler: AsyncIOScheduler)
```

`src/api/dependencies/scheduler.py::get_schedule_service` constructs the extra
`ScheduleRunRepository(session)` from the same session.

New method — `async def delete(self, schedule_id: str, created_by: str) -> None`:

```text
1. row = await repo.get(schedule_id, created_by)
   row is None                     → log schedule_delete_not_found; raise ScheduleNotFoundError (404)
2. schedule.is_deleted is True     → log schedule_delete_conflict; raise ScheduleAlreadyDeletedError (409)
3. run_count = await run_repo.count_for_schedule(schedule_id); log schedule_delete_start
4. unregister (_unregister_job):
     scheduler.state is STATE_STOPPED → raise JobRemovalError            (502)   [see below]
     await asyncio.to_thread(scheduler.remove_job, schedule_id)
     JobLookupError                → swallow, log "already absent"; continue
     any other Exception           → raise JobRemovalError               (502)   [row untouched]
5. if run_count > 0:  _soft_delete(...) ; return
6. try:    removed = await repo.delete_owned(schedule_id, created_by)
   except IntegrityError:          → log schedule_delete_race_fallback; RE-COUNT runs (guarded, so a
                                     failed re-count logs schedule_delete_persist_failed);
                                     _soft_delete(...) with the fresh count; return
   except Exception:               → log schedule_delete_persist_failed; re-raise
   if not removed                  → _raise_for_lost_race(...)
   log schedule_deleted (outcome="removed", run_count=0)

_soft_delete(schedule_id, created_by, run_count):
   try:    marked = await repo.mark_deleted(schedule_id, created_by)
   except Exception:               → log schedule_delete_persist_failed; re-raise
   if not marked                   → _raise_for_lost_race(...)
   log schedule_marked_deleted (outcome="marked_deleted", run_count)

_raise_for_lost_race(schedule_id, created_by):        # a conditional write matched no row
   row = await repo.get(schedule_id, created_by)      # owner-scoped, so a hit means the owner matches
   row is None                     → raise ScheduleNotFoundError        (404)   [winner hard-deleted]
   row present and is_deleted      → raise ScheduleAlreadyDeletedError  (409)   [winner soft-deleted]
   row present and NOT is_deleted  → contradicts the write's own predicate: log at warning with
                                     reason="conditional_write_matched_no_row" and still raise
                                     ScheduleAlreadyDeletedError — never report success
```

Step 4 before steps 5–6 is mandatory (research.md D1). A failure there surfaces to the caller and
leaves a live-looking, non-firing schedule that a retried `DELETE` completes.

**Why step 4 rejects a stopped scheduler.** `BaseScheduler.remove_job` short-circuits when
`state == STATE_STOPPED`: it searches only the in-memory `_pending_jobs` list and raises
`JobLookupError` **without touching the persistent job store**. Since step 4 treats
`JobLookupError` as success, a stopped scheduler would otherwise produce a 204 and delete the row
while the `apscheduler_jobs` entry survives and the worker keeps firing it — the exact SC-006
violation the ordering exists to prevent. The API happens to be safe today because
`src/api/app.py` calls `scheduler.start(paused=True)` (`STATE_PAUSED`, not stopped), but that
coupling is invisible at the call site, so the check is explicit.

**Why a lost race is not always 409.** `rowcount == 0` also occurs when the winner *hard*-deleted
the row, where 404 is the correct answer and is what a sequential retry returns. Mapping both to
409 would contradict `documentation/api_contract.md`, which defines 409 as "already soft-deleted".

Guards added to existing methods, immediately after the existing `if row is None` check:

| Method | Added guard |
|--------|-------------|
| `update` | `if prior_schedule.is_deleted: raise ScheduleAlreadyDeletedError` |
| `pause` | `if schedule.is_deleted: raise ScheduleAlreadyDeletedError` |
| `resume` | `if schedule.is_deleted: raise ScheduleAlreadyDeletedError` |

Other changes:
- `_to_response` maps `is_deleted=schedule.is_deleted`.
- `list` gains `is_deleted: bool = False`, forwards it to the repository, sets
  `ScheduleListItem.is_deleted`, and **appends** to the pagination links rather than overwriting:
  `paginator.extra_query = ordering.to_query() + ("&is_deleted=true" if is_deleted else "")`.

### Exceptions (`src/lib/exceptions/errors.py`)

```text
class ScheduleAlreadyDeletedError(SchedulerError)  # exists but is soft-deleted → 409
class JobRemovalError(SchedulerError)              # scheduler failed to unregister → 502
```

### Schemas (`src/lib/schemas/schedule.py`)

| Schema | Change |
|--------|--------|
| `ScheduleResponse` | `+ is_deleted: bool` (required in the response) |
| `ScheduleListItem` | `+ is_deleted: bool` (required in the response) |
| `ScheduleRequest` | **no change** — the absence of the field plus Pydantic's default `extra="ignore"` is what implements FR-009 |

### Worker (`src/worker/jobs.py`)

Pre-flight guard at the top of `_execute_scheduled_job_inner`, **before** `create_in_progress`:

```text
schedule = await ScheduleRepository(session).get_by_id(schedule_id)
if schedule is None or schedule.is_deleted:
    logger.info("run_skipped_schedule_deleted", schedule_id=..., reason="schedule_deleted" | "schedule_missing")
    clear_contextvars(); return          # no run record is created
```

A failure of this lookup itself must not silently drop a legitimate run: on an unexpected exception,
log and proceed as today (the FK and `_resolve_credentials` remain as backstops).

## 4. Observability contract (FR-018, SC-008)

Structured events via `src.lib.utils.logging.logger`, all carrying `schedule_id`:

| Event | Level | Additional fields |
|-------|-------|-------------------|
| `schedule_delete_start` | info | `run_count` |
| `schedule_delete_not_found` | info | `reason="not_found_or_not_owned"` \| `"deleted_concurrently"` |
| `schedule_delete_job_removed` | info | — |
| `schedule_delete_job_absent` | info | `reason="job_lookup_error"` |
| `schedule_delete_job_removal_failed` | warning | `reason` (includes `"scheduler_stopped"`) |
| `schedule_deleted` | info | `outcome="removed"`, `run_count=0` |
| `schedule_marked_deleted` | info | `outcome="marked_deleted"`, `run_count` |
| `schedule_delete_race_fallback` | warning | `reason="run_created_concurrently"`, `error` — hard delete blocked by the FK, soft-deleting instead |
| `schedule_delete_persist_failed` | error | `reason` — job removed but the row was not updated; retry required |
| `schedule_delete_conflict` | info / warning | `reason="already_deleted"` (rejected before anything was touched) \| `"deleted_concurrently"` (lost race, job already unregistered) \| `"conditional_write_matched_no_row"` (warning — unexpected state) |
| `run_skipped_schedule_deleted` | info | `reason="schedule_deleted"` \| `"schedule_missing"` (worker) |
| `schedule_delete_check_failed` | warning | `reason` — the worker's pre-flight lookup failed; the run proceeds (fail-open) |

`schedule_delete_not_found` is required, not optional: FR-018 and SC-008 demand that **every**
attempt be traceable, and an unknown or foreign id is both the most common rejection and the one
that matters for spotting probing of other users' schedule ids.

On the race-fallback path the run count is **re-read** before `schedule_marked_deleted` is emitted.
Reporting the stale `run_count=0` alongside "marked deleted because history exists" would be
self-contradictory and would defeat the diagnostic purpose FR-018 assigns to the field.

No payload content, credentials, or tokens appear in any of these events.

## 5. Test case inventory (drives TDD)

**Model / migration**
1. `is_deleted` is `NOT NULL` with server default `false`; a newly created schedule reads `False`.
2. Upgrade on a database with pre-existing schedules backfills every row to `false`; downgrade drops
   the column.

**Schemas**
3. `ScheduleResponse` / `ScheduleListItem` serialise `is_deleted`.
4. `ScheduleRequest` ignores an `is_deleted` key in the body (no error, no attribute).

**Repository — schedules**
5. `delete_owned` removes an owned row and returns `True`.
6. `delete_owned` returns `False` for a wrong owner, an unknown id, and an already-soft-deleted row.
7. `delete_owned` raises `IntegrityError` when runs exist (real DB, FK RESTRICT).
8. `mark_deleted` flips the flag once and returns `True`; a second call returns `False`.
9. `mark_deleted` returns `False` for a wrong owner; `updated_at` advances on success.
10. `list(is_deleted=False)` excludes soft-deleted rows; `list(is_deleted=True)` returns only them.
11. The `count` in the returned page reflects the `is_deleted` filter, not the unfiltered total.
12. The `is_deleted` filter composes with name filter, metadata filter, and ordering.
13. `get` still returns a soft-deleted schedule.

**Repository — runs**
14. `count_for_schedule` counts runs of every status, and returns 0 for an unknown schedule.
15. `list_for_schedule` totals are unchanged after the refactor.

**Service**
16. No runs → `remove_job` called once, row gone, no soft mark.
17. Runs exist → `remove_job` called once, row present with `is_deleted=True`, runs untouched.
18. `remove_job` raising `JobLookupError` still completes the delete (both branches).
19. `remove_job` raising any other error → `JobRemovalError`, and **no** database write occurred.
20. Persist failure after a successful unregister → error propagates; a second `delete` call succeeds
    (`JobLookupError` path).
21. `delete_owned` raising `IntegrityError` → falls back to `mark_deleted`, logs the race event, and
    the request succeeds.
22. `mark_deleted`/`delete_owned` returning `False` → `ScheduleAlreadyDeletedError`.
23. Unknown or foreign schedule → `ScheduleNotFoundError`, scheduler never touched.
24. Soft-deleted schedule → `update`, `pause`, `resume`, and `delete` all raise
    `ScheduleAlreadyDeletedError`, and the scheduler is never touched.
25. `list` echoes `is_deleted=true` into `next`/`previous` links, and omits it when `false`.

**Endpoint**
26. `DELETE` → 204 with an empty body, for both branches.
27. 404 for unknown/foreign; 409 for already soft-deleted; 502 on `JobRemovalError`.
28. `GET /{id}` of a soft-deleted schedule → 200, `is_deleted: true`, `next_run_time: null`.
29. `GET /` default excludes it; `?is_deleted=true` returns it; `?is_deleted=maybe` → 422.
30. `PUT` with `"is_deleted": true` in the body on a live schedule → 200 and `is_deleted: false`.
31. Run history endpoints still return 200 with all runs for a soft-deleted schedule.

**Worker**
32. A due job for a soft-deleted schedule creates **no** run record and logs the skip event.
33. A due job for a removed schedule creates no run record and logs the skip event.

**MCP**
34. The exposed tool set includes the new delete tool (`tests/unit/api/mcp/test_mcp_tools.py`).

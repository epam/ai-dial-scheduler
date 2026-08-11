# Code Review Report — 035-schedule-delete

**Reviewed**: uncommitted working-tree changes on branch `035-schedule-delete`
**Base commit**: `53d74e1` (`development`)
**Snapshot**: `git diff HEAD` md5 `c3a428b914d73da468a25616b0c1ca61`
(blobs: `schedule_service.py` `21eec21`, `schedule_repository.py` `27be94c`, `jobs.py` `9094a58`)
**Artifacts consulted**: `spec.md`, `plan.md`, `research.md`, `data-model.md`,
`.specify/memory/constitution.md`, `CLAUDE.md`
**Date**: 2026-08-10

> Note on a moving target: `src/lib/repositories/schedule_repository.py` and
> `tests/integration/test_schedule_delete.py` were modified in the working tree *during* this
> review (mtimes 13:50:09 / 13:49:38). All findings below are against the snapshot hashed above,
> which was stable from 13:57 onward. An earlier read of `delete_owned` (blob `9c60826`) had **no**
> `IntegrityError` rollback; the reviewed snapshot does. See M1.

## Verdict

**Approve with minor changes.** The two-branch delete is correctly ordered and the concurrency
story holds up under real-database testing. No blocker- or major-severity defects were found in
the delete algorithm, the conditional writes, the worker guard, or the list filter. The findings
below are one observability requirement that is not met (FR-018/SC-008 for the not-found path),
one misleading log field, and a set of robustness/consistency nits.

### Verification performed (not just reading)

| Gate | Result |
|------|--------|
| `uv run pytest` | 824 passed, 149 skipped |
| `uv run pytest tests/integration` with `TEST_DB_URL` → real PostgreSQL 16 (throwaway container) | **158 passed** — including `TestRaceFallbackOnRealSession` |
| `uv run ruff check src tests` | All checks passed |
| `uv run ruff format --check src tests` | 161 files already formatted |
| `uv run mypy src` | Success, 80 files |
| `alembic upgrade head` on an empty DB | full chain applies; `is_deleted boolean not null default false` |
| `alembic check` | "No new upgrade operations detected" (no model/migration drift) |
| `alembic downgrade -1` → insert legacy row → `upgrade head` | pre-existing row backfilled to `false` |
| `alembic heads` chain | single head; `e5f3c4d6a7b8` → `d4e2b3c5f6a7` correct |

Note that the CI-visible test run **skips** every integration test unless `TEST_DB_URL` is set
(`tests/integration/conftest.py:13-19`). The FK/rollback behaviour reviewed below is therefore
only proven when that variable is exported — worth wiring into the pipeline (see N6).

---

## Blockers

**None.**

The specific hazard raised for review — "is the AsyncSession still usable for the follow-up
`mark_deleted` UPDATE after a failed DELETE, since PostgreSQL aborts the transaction on statement
error" — is real, and it *is* handled:

`src/lib/repositories/schedule_repository.py:94-99`
```python
try:
    result = cast(CursorResult[Any], await self._session.execute(stmt))
    await self._session.commit()
except IntegrityError:
    await self._session.rollback()
    raise
```

I confirmed both halves of this empirically against PostgreSQL 16:

- **Without** the rollback the fallback is broken. A minimal reproduction (same session, FK
  `ON DELETE RESTRICT`, DELETE then UPDATE) fails with
  `asyncpg.exceptions.InFailedSQLTransactionError: current transaction is aborted, commands
  ignored until end of transaction block`, and the soft delete never lands.
- **With** it, the real code path works: driving `ScheduleService.delete` against a real session
  with a stale run count of 0 produced
  `schedule_delete_race_fallback` → `schedule_marked_deleted`, left the row present with
  `is_deleted = true`, and preserved the concurrently created run.
- This is dialect-specific and cannot be caught by the unit tests (mocked repo) — on SQLite the
  follow-up UPDATE succeeds even without a rollback. The dedicated integration test
  `tests/integration/test_schedule_delete.py:241-277` is the only thing that pins it. Keep it.

No sequence was found that presents a schedule as deleted while it can still fire, and none that
can destroy run history:

- `is_deleted` is only ever written **after** a successful (or `JobLookupError`-tolerated)
  unregister — `schedule_service.py:258` precedes `:260-286`. A persist failure therefore leaves
  the benign "live-looking, non-firing" state, which a retry fixes via the `JobLookupError` path.
- Hard delete is `ON DELETE RESTRICT`-guarded at the database level; verified for all four run
  statuses (`in_progress` included) in `test_schedule_delete.py:135-162`. Run history cannot be
  lost even if the count is wrong.
- Worker/API interleaving is safe in both directions: a run INSERT takes a `FOR KEY SHARE` lock on
  the `schedules` row, so it serialises with the DELETE. Run-insert-first → DELETE raises
  `IntegrityError` → soft-delete fallback (run preserved). DELETE-first → run INSERT fails on the
  FK → `run_create_failed`, no orphan.
- Concurrent double-delete: both writes carry `AND is_deleted = false` and are decided by
  `rowcount` under READ COMMITTED, where the blocked statement re-evaluates the predicate after
  the winner commits. Exactly one wins; the loser gets 409. Pinned by
  `test_schedule_delete.py:202-213`.

---

## Major

### M1 — A rejected delete (404 path) emits no log at all: FR-018 / SC-008 not met

`src/lib/services/schedule_service.py:247-249`

```python
row = await self._repo.get(schedule_id, created_by)
if row is None:
    raise ScheduleNotFoundError(schedule_id)
```

FR-018 requires "a structured log entry for **every** deletion attempt … the outcome including a
reason when the attempt fails", and SC-008 requires every attempt, "successful or failed", to be
traceable "in 100% of cases". `schedule_delete_start` is only reached at line 256, *after* both
guards. The already-deleted guard does log (`schedule_delete_conflict`, line 252), but the
unknown/foreign-schedule rejection — the most common failure and the one that matters for
detecting probing of other users' ids — produces no event whatsoever. Constitution II
(NON-NEGOTIABLE) points the same way for a "discard" decision.

`data-model.md` §4 shares the gap: its event table has no not-found row, so the implementation is
faithful to the design and the design is what misses the requirement.

Fix (one line, plus a test):

```python
if row is None:
    logger.info("schedule_delete_not_found", schedule_id=schedule_id, reason="not_found_or_not_owned")
    raise ScheduleNotFoundError(schedule_id)
```

and add the event to `data-model.md` §4.

---

## Minor

### m1 — `schedule_marked_deleted` reports `run_count=0` on the race-fallback path

`src/lib/services/schedule_service.py:269` passes the *stale* `run_count` (which is 0 by
definition on this branch) into `_mark_deleted`, so the event reads:

```
schedule_delete_race_fallback  reason=run_created_concurrently
schedule_marked_deleted        outcome=marked_deleted run_count=0
```

(verbatim from my run against PostgreSQL). A log reader sees "marked deleted because it has run
history" next to "run_count=0", which is self-contradictory and defeats the diagnostic purpose
FR-018 assigns to the field. Either re-count after the FK block, or pass a sentinel that reads
honestly, e.g. `await self._mark_deleted(schedule_id, created_by, run_count=-1)` is worse than
re-reading:

```python
except IntegrityError:
    logger.warning("schedule_delete_race_fallback", schedule_id=schedule_id, reason="run_created_concurrently")
    run_count = await self._run_repo.count_for_schedule(schedule_id)
    await self._mark_deleted(schedule_id, created_by, run_count)
    return
```

### m2 — The race-fallback `mark_deleted` is not wrapped by `schedule_delete_persist_failed`

`src/lib/services/schedule_service.py:263-270`. The soft branch at `:280-286` wraps
`_mark_deleted` so an unexpected persist failure is logged as `schedule_delete_persist_failed`
before propagating. The identical call inside the `IntegrityError` handler is not wrapped, so the
worst case on that path — job already unregistered, DELETE blocked, UPDATE then fails — escapes to
a 500 with no `persist_failed` event, i.e. exactly the state that most needs a log ("retry
required"). Extract the wrapping into a small helper and use it on both paths.

### m3 — `409 Conflict` is returned for a row that is actually gone

`src/lib/services/schedule_service.py:274-276` and `:211-213` map `rowcount == 0` to
`ScheduleAlreadyDeletedError` → 409 "Schedule already deleted". That is right when the row was
soft-deleted concurrently, but the same `rowcount == 0` also occurs when a concurrent request
**hard-deleted** the row, where the correct answer is 404 (which is what a sequential retry
returns — pinned by `tests/integration/test_schedules_api.py`
`test_second_delete_returns_404`). `documentation/api_contract.md` states 409 means "the schedule
is already soft-deleted", so the concurrent-loser response contradicts the published contract.
This is inherited from `data-model.md` §3 and only observable inside the race window; if it is
worth closing, re-read the row on `rowcount == 0` and raise `ScheduleNotFoundError` when it is
absent. Otherwise document that 409 also covers "deleted concurrently".

### m4 — `JobLookupError` is treated as success unconditionally, which is only safe because the API scheduler is started

`src/lib/services/schedule_service.py:200-202`. APScheduler's `BaseScheduler.remove_job` (verified
in the installed source) short-circuits when `self.state == STATE_STOPPED`: it searches only
`_pending_jobs` and raises `JobLookupError` **without touching the persistent job store**.
Swallowing that as success would return 204 and delete the row while the `apscheduler_jobs` row
survives and the worker keeps firing it — precisely the SC-006 violation the ordering is designed
to prevent.

Today this is safe: `src/api/app.py:28` calls `scheduler.start(paused=True)`, so the state is
`STATE_PAUSED`, not `STATE_STOPPED`. The coupling is invisible at the call site and untestable
through the unit tests (they mock the scheduler). Consider making it explicit:

```python
if self._scheduler.state == STATE_STOPPED:
    raise JobRemovalError("scheduler is not running; cannot unregister job")
```

placed before the `remove_job` call (a raise, not an `assert` — the constitution forbids `assert`
in production code).

### m5 — 502 body forwards the raw scheduler exception text to the client

`src/api/v1/endpoints/schedules.py:255`: `HTTPException(status_code=502, detail=str(exc))`, fed by
`JobRemovalError(str(exc))` at `schedule_service.py:205`. The wrapped exception comes from
APScheduler's `SQLAlchemyJobStore`, so the detail can carry SQL text and connection diagnostics.
It follows the existing `JobRegistrationError` pattern (`:115`, `:140`), so this is not a new
policy break, but it does add a third instance. Prefer a fixed message to the client
("Failed to unregister the schedule's job; retry") and keep `str(exc)` in the log only.

### m6 — `list` parameter counts now exceed the constitution's 2–3 limit, undocumented

`src/lib/services/schedule_service.py:288-295` (6 parameters) and
`src/lib/repositories/schedule_repository.py:211-220` (7). Constitution V requires "at most 2–3
parameters; group additional data into Pydantic models", and states that complexity violations
must appear in the plan's Complexity Tracking table — `plan.md` leaves that table intentionally
empty and its Constitution Check only accounts for `ScheduleService.__init__` growing to 3. The
drift is pre-existing (metadata/name filters, ordering) and `is_deleted` is a genuinely simple
addition, so I would not restructure it for this feature; but either record it in Complexity
Tracking or fold the filters into a `ScheduleListFilters` model in a follow-up.

### m7 — The worker guard leaves a narrow check-then-insert window

`src/worker/jobs.py:193-196` correctly runs **before** `create_in_progress` (`:200`), which is the
part that matters — for a hard-deleted schedule the FK would reject the insert anyway, and for a
soft-deleted one nothing else would stop it. It is still not atomic: a soft delete committing
between the guard's read and the INSERT yields a run record for a deleted schedule, which is
strictly a FR-006 miss. In practice this collapses into the race the spec already tolerates (the
API's own hard→soft fallback exists precisely to accept a run that appears at delete time), and
closing it would need locking on the run-creation path. Recommend documenting it in
`data-model.md` §2 rather than changing code.

---

## Nits

- **N1** — `src/worker/jobs.py:163` `_schedule_is_deleted` returns `True` for a *missing*
  schedule too; the name reads as a pure flag check. `_should_skip_run` (or
  `_schedule_is_gone_or_deleted`) would match the semantics. The docstring does explain it.
- **N2** — `src/worker/jobs.py:176` logs `schedule_delete_check_failed`, an event not listed in
  `data-model.md` §4. Harmless, but the observability contract should enumerate it.
- **N3** — `src/lib/services/schedule_service.py:282-283` (`except ScheduleAlreadyDeletedError:
  raise`) exists only to keep the conflict out of the `persist_failed` log. Correct, but the
  intent is non-obvious; a one-line comment or a `_persist_or_log` helper would read better.
- **N4** — `src/lib/services/schedule_service.py:355`, `:373` use `row[0].is_deleted` in
  `pause`/`resume` while `delete` unpacks into `schedule, _`. Unify on unpacking.
- **N5** — `except IntegrityError` in `delete_owned` assumes the only integrity failure a
  conditional DELETE can raise is the run-history FK. True for the current schema; if another FK
  ever references `schedules`, the fallback would silently soft-delete on an unrelated violation.
  Inspecting `exc.orig` for the constraint name would make the intent explicit.
- **N6** — Consider exporting `TEST_DB_URL` in CI. Every guarantee unique to this feature (FK
  RESTRICT, the transaction rollback, `updated_at` refresh via `onupdate`, count-vs-filter) lives
  in integration tests that currently skip by default; the unit suite alone would go green with
  the rollback removed.
- **N7** — `src/lib/repositories/schedule_repository.py:98` `session.rollback()` also expires the
  identity map, so any ORM object loaded earlier in the request (e.g. the `schedule` read at
  `schedule_service.py:247`) becomes stale. Nothing reads it after the fallback today, and a lazy
  refresh in async context would fail loudly (`MissingGreenlet`) rather than silently, so this is
  informational only — but a future edit that touches `schedule.*` after a fallback will break.

---

## Reviewed and clean

- **Two-branch delete ordering (review focus 1)** — unregister → persist, `JobLookupError`
  tolerated, `IntegrityError` → soft-delete fallback, `ScheduleAlreadyDeletedError` on both lost
  races. Matches `data-model.md` §3 step-for-step. No history-destroying or
  deleted-but-still-firing sequence found.
- **Conditional writes (review focus 2)** — single-statement DELETE/UPDATE with
  `AND is_deleted = false`, decided by `rowcount == 1`; no read-then-write race; rollback present
  and empirically necessary; `updated_at` advances via the column's `onupdate` (verified against
  PostgreSQL).
- **Worker pre-flight guard (review focus 3)** — placed before `create_in_progress`, skips with no
  run record, distinguishes `schedule_deleted` / `schedule_missing`, and deliberately fails open on
  lookup errors. Pre-existing "schedule vanished mid-run" tests were *adapted* (two-element
  `side_effect`) rather than deleted, so the credentials-stage coverage survives.
- **List filter (review focus 4)** — `conditions` is built once at
  `schedule_repository.py:245` and applied to **both** the count statement (`:254`) and the page
  statement (`:267`), so `count` matches the filter. `schedule_service.py:312` **appends** to
  `paginator.extra_query` instead of overwriting, and `ordering.to_query()` is never empty, so the
  `&`-prefixed concatenation is well-formed; `Paginator.paginate` adds the leading `&`. Verified
  end-to-end (`"is_deleted=true" in response.json()["next"]`, `count == 2`).
- **Constitution (review focus 5)** — `lib/` imports nothing from `src.api`/`src.worker` (grepped,
  zero hits); no `print()` and no `assert` anywhere in `src/`; all scheduler calls go through
  `asyncio.to_thread` so nothing blocks the loop; structured logging exclusively via
  `src.lib.utils.logging.logger`; Google-style docstrings on every new public method; full type
  hints with `mypy` clean; no raw SQL on the feature path (Core `delete()`/`update()` constructs);
  no `.env`-family reads; migration present, single-headed, drift-free.
- **`is_deleted` immutability (FR-009)** — absent from `ScheduleRequest`; Pydantic's default
  `extra="ignore"` drops it; asserted at both unit (`test_schedules.py`
  `TestIsDeletedIsNotWritable`) and integration level, including
  `assert not hasattr(submitted, "is_deleted")`.
- **FR-016 / FR-019** — `get`/`get_by_id` stay unfiltered so run history remains reachable
  (pinned by an integration test that exists precisely to prevent a "helpful" future filter);
  `next_run_time` is `null` for soft-deleted schedules because the job row is gone.
- **MCP exposure** — the new destructive tool is a recorded decision in `research.md`, and both
  `test_mcp_tools.py` and `test_server.py` were updated to nine tools.
- **Route registration order** — the `DELETE` handler is appended at EOF (`:230`), safely after
  both `router.routes[-1].description +=` lines (`:118`, `:143`).
- **Docs** — `api_contract.md` and `db_tables.md` match the implemented behaviour, including the
  204/404/409/502 table, the filter semantics, and the FK note. The pre-existing "TODO: discuss if
  we really want to delete something" is properly replaced.

## Intent alignment

Every functional requirement is implemented as specified, with two documented deviations that
originate in the design artifacts rather than the code:

1. **FR-018 / SC-008** are not fully met on the not-found path — see M1. `data-model.md` §4 has the
   same omission.
2. **FR-015 / FR-002 boundary**: the concurrency loser receives 409 where the row may already be
   gone (404 semantics) — see m3. Prescribed by `data-model.md` §3.

`research.md` D1–D9 are all reflected in the code as written, including D7's deliberate refinement
of the spec's "reuse the not-found error style" assumption into 409, and D6's no-index decision
(pinned by a model test).

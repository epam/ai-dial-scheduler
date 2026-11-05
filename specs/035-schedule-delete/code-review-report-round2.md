# Code Review Report (Round 2) — 035-schedule-delete

**Reviewed**: uncommitted working-tree changes on branch `035-schedule-delete`
**Base commit**: `53d74e1` (`development`)
**Scope**: `git diff HEAD` + untracked files (`migrations/versions/20260810_1200_e5f3c4d6a7b8_add_is_deleted_to_schedules.py`,
`tests/integration/test_schedule_delete.py`, `tests/unit/migrations/test_add_is_deleted.py`, `specs/035-schedule-delete/`)
**Prior round**: `specs/035-schedule-delete/code-review-report-round1.md` (M1, m1–m7, N1–N7)
**Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `research.md`, `CLAUDE.md`
**Date**: 2026-08-10

## Verdict

**Approve.** All fourteen actioned round-1 findings are genuinely fixed — not papered over — and each
carries a dedicated regression test. The restructured `delete` (`_raise_for_lost_race`,
`_soft_delete`, `_unregister_job`) is a real improvement in readability and its exception paths are
sound: no double-logging, no lost exception, no mypy/flow friction, and the `session.rollback()`
identity-map hazard from N7 is provably not triggered. The one deliberately deferred item (N6) was
deferred for the wrong stated reason but is still the right call.

Four new minor findings, all introduced by the fixes themselves and none affecting the 204/404/409/502
contract: three are observability/robustness gaps at the edges of the new control flow, one is design-doc
drift. **No new blocker- or major-severity defects.**

### Gates re-run (this reviewer, not inherited)

| Gate | Result |
|------|--------|
| `uv run pytest` with `TEST_DB_URL` → throwaway PostgreSQL 16.14 container | **979 passed, 0 skipped, 0 failed** (39s) |
| `uv run pytest` without `TEST_DB_URL` | 830 passed, **149 skipped** |
| `uv run ruff check src tests` | All checks passed |
| `uv run ruff format --check src tests` | 161 files already formatted |
| `uv run mypy src` | Success, no issues in 80 source files |

The container (`postgres:16`, `pg-review-035`) was removed after the run; nothing was left behind and
no source or test file was modified.

### Out-of-tree empirical probe (m1 + N7)

The repo's own `TestRaceFallbackOnRealSession` stubs `count_for_schedule` with
`AsyncMock(return_value=0)` (`tests/integration/test_schedule_delete.py:263-264`), so the m1 re-count
never issues a real query there. I ran the fallback with the **real** `ScheduleRunRepository` against
PostgreSQL 16 from a scratchpad script (no repo file touched), with two genuine runs and only the
*first* count forced to a stale 0:

```
count calls         : [0, 2]
events              : schedule_delete_start(run_count=0) → schedule_delete_job_removed
                      → schedule_delete_race_fallback → schedule_marked_deleted(run_count=2)
row survives        : True      is_deleted : True      runs preserved : 2
stale obj is_deleted: True      (pre-rollback ORM object, read after the fallback — fresh, no MissingGreenlet)
```

This confirms (a) the post-rollback SELECT works on the aborted-then-rolled-back session, (b)
`schedule_marked_deleted` now reports the true count, and (c) N7's stale-identity-map concern does not
materialise.

---

## Round-1 finding verification

| ID | Status | Evidence |
|----|--------|----------|
| **M1** — 404 path emits no log | **FIXED** | `src/lib/services/schedule_service.py:284` logs `schedule_delete_not_found` with `reason="not_found_or_not_owned"` before raising. Event added to the observability contract at `specs/035-schedule-delete/data-model.md:173`, with a `:185-187` note recording *why* it is mandatory (FR-018/SC-008). Pinned by `tests/unit/lib/services/test_schedule_service.py:1067` (`test_not_found_attempt_is_logged`), which asserts exactly one event and its `reason`/`schedule_id`. |
| **m1** — stale `run_count=0` on the fallback | **FIXED** | `schedule_service.py:311` re-counts inside the `except IntegrityError` handler before calling `_soft_delete`. Unit-pinned at `test_schedule_service.py:1163` (`test_fallback_recounts_so_the_log_is_not_self_contradictory`, asserts `await_count == 2` and `run_count == 1`) and documented at `data-model.md:189-191`. Verified end-to-end against real PostgreSQL above (`0 → 2`). |
| **m2** — fallback soft delete not wrapped | **FIXED** | The wrapping was extracted into `_soft_delete` (`schedule_service.py:235-254`), whose `try/except` logs `schedule_delete_persist_failed` and re-raises. It is now used by **both** persist paths — the soft branch (`:297`) and the `IntegrityError` fallback (`:312`) — so the duplication that caused the omission is gone. Pinned at `test_schedule_service.py:1189` (`test_fallback_persist_failure_is_logged_as_persist_failed`). |
| **m3** — 409 for a row that is gone | **FIXED** | `_raise_for_lost_race` (`schedule_service.py:221-233`) re-reads and returns 404 when the row is absent (`:230-231`, `reason="deleted_concurrently"`), 409 when present (`:232-233`). Called from both `rowcount == 0` sites (`:248`, `:318`). Both outcomes pinned: `test_schedule_service.py:1040` (409) and `:1055` (404). `documentation/api_contract.md:196` already covers it ("unknown schedule, another user's schedule, or one already hard-deleted"). See **I1** and **I2** for two residual gaps. |
| **m4** — `STATE_STOPPED` swallowed as success | **FIXED** | `schedule_service.py:204-210` rejects a stopped scheduler with `JobRemovalError` (a raise, not an `assert`) *before* `remove_job`, and logs `schedule_delete_job_removal_failed` with `reason="scheduler_stopped"`. The premise is correct — I re-read the installed `BaseScheduler.remove_job`, which searches only `_pending_jobs` when `state == STATE_STOPPED` and raises `JobLookupError` without touching the job store. The coupling is now documented in the `_unregister_job` docstring (`:196-199`) instead of being invisible. Pinned at `test_schedule_service.py:1085`, which asserts `to_thread` is never awaited and neither write is attempted. |
| **m5** — 502 forwards raw scheduler text | **FIXED** | `src/api/v1/endpoints/schedules.py:258-261` returns a fixed message ("Failed to unregister the schedule's job; nothing was changed. Retry the request."), with a comment (`:255-257`) explaining that the raw text stays in the service log. `str(exc)` is retained only at `schedule_service.py:217`. Note this fixes the new endpoint only; the two pre-existing `JobRegistrationError` sites still forward `str(exc)` — correctly left out of scope. |
| **m6** — `list` parameter count undocumented | **FIXED** | `plan.md:184-189` Complexity Tracking now carries both rows (`list` 6/7 params, `__init__` 3) with the rejected alternative spelled out. One leftover contradiction — see **N-new-2**. |
| **m7** — worker check-then-insert window | **FIXED (documented, as recommended)** | `data-model.md:61-67` "Accepted residual race" records the FR-006 miss, why it collapses into a race the spec already tolerates, and why locking is not justified. Code unchanged, which was the recommendation. |
| **N1** — `_schedule_is_deleted` misnamed | **FIXED** | Renamed to `_should_skip_run` (`src/worker/jobs.py:163`), call site `:194`. |
| **N2** — `schedule_delete_check_failed` unlisted | **FIXED** | `data-model.md:183` lists it (warning, fail-open). |
| **N3** — opaque `except ScheduleAlreadyDeletedError: raise` | **FIXED (obsolete by restructure)** | The re-raise clause is gone. `_soft_delete` now calls `_raise_for_lost_race` *outside* the `try` (`schedule_service.py:242-248`), so the conflict can no longer reach the `persist_failed` handler structurally rather than by a filter clause — a better fix than the comment that was suggested. Intent is in the docstring (`:236-241`). |
| **N4** — `row[0].is_deleted` vs unpacking | **FIXED** | `pause` (`:395`) and `resume` (`:418`) both use `schedule, _ = row`, consistent with `delete` (`:286`) and `get` (`:382`). |
| **N5** — blanket `except IntegrityError` | **FIXED (documented)** | `src/lib/repositories/schedule_repository.py:88-91` records that the run-history FK is currently the only constraint referencing `schedules`, and that callers log the original error text so a future second FK would be diagnosable rather than silent. Inspecting `exc.orig` was the alternative; documenting the assumption is proportionate. |
| **N6** — export `TEST_DB_URL` in CI | **NOT FIXED (deliberate)** — right call, wrong reason. See below. |
| **N7** — `rollback()` expires the identity map | **FIXED (documented) / informational** | `schedule_repository.py:84-86` explains the rollback's necessity. The restructure did add a post-rollback read (`_raise_for_lost_race:228`), which is the precise scenario N7 warned about, but it is safe: it re-executes a `select(Schedule)` and inspects **only row presence**, never an attribute, and the incoming row repopulates the expired instance anyway. Probed against real PostgreSQL (above): the pre-rollback ORM object read after the fallback returned the fresh value with no `MissingGreenlet`. Latent risk if a future edit reads attributes — see **I2**. |

### N6 — was skipping it the right call?

**Yes, but the stated justification does not hold.** Only the docker build comes from shared pipeline
templates. The test job is defined **locally in this repository's CI configuration**, so wiring
integration tests in would be a repo-local change of a few lines (a database service container plus a
`TEST_DB_URL` variable), not an edit to shared pipeline infrastructure.

The deferral is nonetheless correct for this branch, on genuine grounds rather than the one given:
enabling a service container changes the runner requirements of a job every pipeline depends on,
which is a cross-cutting infrastructure change that should not ride along in a feature branch and
should be validated on its own.

The underlying risk is real and I re-measured it: **149 tests skip** when `TEST_DB_URL` is unset, and
that set contains the only coverage of this feature's dialect-specific guarantee — the
`session.rollback()` in `delete_owned` (`schedule_repository.py:102-104`), which SQLite does not
require. Recommend a tracked follow-up ticket rather than a code change here.

---

## New defects introduced by the fixes

**No new blockers. No new major-severity defects.** The four items below are minor.

### I1 (Minor) — `data-model.md` §3 pseudo-code was not updated for the m3 and m4 fixes

`specs/035-schedule-delete/data-model.md:110-116`

```text
5. if run_count == 0:
       try:    removed = await repo.delete_owned(schedule_id, created_by)
       except IntegrityError:      → run appeared after step 3; fall through to the soft branch
       if not removed              → raise ScheduleAlreadyDeletedError   (409)   [lost the race]
   else / on IntegrityError fallback:
       marked = await repo.mark_deleted(schedule_id, created_by)
       if not marked               → raise ScheduleAlreadyDeletedError   (409)   [lost the race]
```

The shipped code raises `ScheduleNotFoundError` (404) at both of these points when the re-read shows
the row is gone (`schedule_service.py:229-231`). Step 4 (`:107-109`) likewise still describes a bare
`remove_job` with no `STATE_STOPPED` pre-check. The observability table was updated for m1, M1 and m4
(`:173`, `:176`, `:189-191`) but §3 was not, so the design document now contradicts the implementation
on two points.

This matters more than ordinary doc drift here: M1's own root-cause note was *"the implementation is
faithful to the design and the design is what misses the requirement."* Leaving §3 stale re-creates
exactly that trap for the next reader, who will treat the pseudo-code as the contract.

Fix — update `:113` and `:116` to
`if not removed/marked → re-read: absent → ScheduleNotFoundError (404) | present → ScheduleAlreadyDeletedError (409)`,
and add the stopped-scheduler rejection to step 4.

### I2 (Minor) — `_raise_for_lost_race` infers 409 from row presence and never checks `is_deleted`

`src/lib/services/schedule_service.py:228-233`

```python
row = await self._repo.get(schedule_id, created_by)
if row is None:
    ...
    raise ScheduleNotFoundError(schedule_id)
logger.info("schedule_delete_conflict", schedule_id=schedule_id, reason="already_deleted")
raise ScheduleAlreadyDeletedError(schedule_id)
```

"Row present" is treated as proof of `is_deleted = true`. That inference is sound *today* only because
both conditional writes carry `AND is_deleted = false` and `created_by` was validated by the caller's
earlier read — i.e. it depends on two invariants held in other functions, and reports a hard 409 to the
client if either ever slips.

Concrete evidence that the assumption is untested rather than merely implicit:
`tests/unit/lib/services/test_schedule_service.py:1209`
(`test_lost_race_on_soft_branch_raises_already_deleted`) builds its repo from `_live_repo()` (`:930-937`),
whose `repo.get` returns a schedule with **`is_deleted = False`**, and still asserts
`ScheduleAlreadyDeletedError`. The test passes precisely because the flag is never consulted.

Fix — make the classification explicit, which costs nothing and is safe post-rollback (the re-read
repopulates the expired instance; verified empirically above):

```python
schedule, _ = row
if not schedule.is_deleted:
    logger.error("schedule_delete_unexpected_no_match", schedule_id=schedule_id)
raise ScheduleAlreadyDeletedError(schedule_id)
```

and give the 409 test a genuinely soft-deleted re-read row, as `:1040` already does.

### I3 (Minor) — the m1 re-count is itself outside the `persist_failed` guard

`src/lib/services/schedule_service.py:311`

```python
except IntegrityError as exc:
    logger.warning("schedule_delete_race_fallback", ...)
    run_count = await self._run_repo.count_for_schedule(schedule_id)   # <-- unguarded
    await self._soft_delete(schedule_id, created_by, run_count)
```

m2 was raised because the worst state on this path — job already unregistered, DELETE blocked, row not
yet updated — must emit `schedule_delete_persist_failed` so an operator knows a retry is required. The
m2 fix wraps `mark_deleted`, but the m1 fix then inserted an unguarded `await` one line *earlier*, in
the same window. If that SELECT fails (connection lost, statement timeout), the request 500s having
logged only `schedule_delete_race_fallback` — reintroducing the gap m2 closed, one statement further up.

The same shape exists at `:291`: the initial `count_for_schedule` precedes `schedule_delete_start`
(`:292`), so a count failure produces **no** delete event at all, an FR-018/SC-008 hole. That one is
pre-existing and benign (nothing has been mutated yet), but it argues for the same treatment.

Fix — move the re-count inside `_soft_delete`, or wrap it:

```python
except IntegrityError as exc:
    logger.warning("schedule_delete_race_fallback", ...)
    try:
        run_count = await self._run_repo.count_for_schedule(schedule_id)
    except Exception as count_exc:
        logger.error("schedule_delete_persist_failed", schedule_id=schedule_id, reason=str(count_exc))
        raise
    await self._soft_delete(schedule_id, created_by, run_count)
```

### I4 (Minor) — `schedule_delete_conflict` is now emitted from two operationally different states with identical fields

`src/lib/services/schedule_service.py:288` (pre-write fast-path guard) and `:232` (lost race) both emit:

```python
logger.info("schedule_delete_conflict", schedule_id=schedule_id, reason="already_deleted")
```

These are not equivalent. At `:288` nothing has happened — the request was rejected before touching the
scheduler. At `:232` **this request has already unregistered the job** (`:294`) and then lost the
conditional write. The outcome is still consistent (the winner unregistered it too, and the loser's
duplicate `remove_job` is tolerated via `JobLookupError`), but an operator reading logs cannot tell a
cheap up-front rejection from a partially executed request.

The m3 fix got this right for the sibling event — `schedule_delete_not_found` distinguishes
`not_found_or_not_owned` (`:284`) from `deleted_concurrently` (`:230`) — so the asymmetry looks like an
oversight rather than a decision.

Fix — use `reason="deleted_concurrently"` at `:232`, and add the second value to the
`schedule_delete_conflict` row of `data-model.md:181` (it currently documents only `"already_deleted"`).

---

## New nits

- **N-new-1** — `tests/integration/test_schedule_delete.py:263-264` replaces `count_for_schedule` with
  `AsyncMock(return_value=0)`, so **both** counts are stubbed and the m1 re-count never executes a real
  post-rollback SELECT in the suite; the test also asserts nothing about `run_count`. My out-of-tree
  probe covered it (`0 → 2`), but the guarantee should live in the repo. Use
  `AsyncMock(side_effect=[0, <real>])` — or leave the second call real — and assert
  `schedule_marked_deleted.run_count == 1`. This is the only integration test pinning the whole fallback,
  so it is worth strengthening.
- **N-new-2** — `specs/035-schedule-delete/plan.md:93` still reads "PASS — no violations. Complexity
  Tracking not required." while `:184-189` now contains two tracked violations. Reword `:93` to point at
  the table.
- **N-new-3** — `schedule_service.py:305-310` logs `error=str(exc)` for the `IntegrityError`. A
  SQLAlchemy `IntegrityError.__str__` embeds the full SQL statement **and the bound parameters**, which
  here include `created_by` (the user id). It is log-only (correctly kept out of the 502 body per m5) and
  the parameters carry no secrets, but the event is unnecessarily large and inconsistent with the tight
  fields elsewhere. Consider `str(exc.orig)` or the constraint name — which would also serve N5's
  suggestion of making the FK attribution explicit.
- **N-new-4** *(pre-existing, but newly asymmetric)* — the m4 fix guards `remove_job` against
  `STATE_STOPPED`; `_register_job` (`schedule_service.py:46-57`) has no counterpart. I verified in the
  installed `BaseScheduler.add_job` that a stopped scheduler only appends to `_pending_jobs` ("Adding job
  tentatively") and never reaches the job store, so `create`/`update` would return success for a job that
  was never persisted — the mirror image of the hazard m4 closed. Unreachable today
  (`src/api/app.py:28` starts the scheduler paused, and `src/api/dependencies/scheduler.py:24` is the only
  `ScheduleService` construction site, always fed from `app.state.scheduler`), and out of scope for a
  delete feature. Noted only so the one-sidedness is a recorded decision.

---

## Re-verified clean (round-2 spot checks of the restructure)

- **Exception-path integrity of the new `delete`** — `:317-319` sits outside the `try`, so
  `ScheduleAlreadyDeletedError`/`ScheduleNotFoundError` from `_raise_for_lost_race` can never be
  swallowed by the `except Exception` at `:314`. Likewise `_soft_delete` raising from inside the
  `except IntegrityError` handler (`:312`) is not caught by the sibling `except Exception`, so
  `schedule_delete_persist_failed` is emitted exactly once on every path. No double-logging found on any
  of the eight reachable outcomes.
- **`NoReturn` and flow analysis** — `_raise_for_lost_race` is `async def ... -> NoReturn` and always
  raises on both branches, so no path falls through to `logger.info("schedule_deleted", ...)` (`:319`) or
  to `schedule_marked_deleted` (`:249`). No mypy friction: `warn_unreachable` is not enabled
  (`pyproject.toml:94-99`), and both call sites are inside `if` blocks so nothing would be flagged even
  if it were. `uv run mypy src` is clean.
- **Ordering invariant preserved by the restructure** — `_unregister_job` (`:294`) still precedes every
  write (`:297`, `:301`, `:312`), and the stopped-scheduler rejection is *before* `remove_job`, so the
  502 path still guarantees "nothing was changed".
- **Post-rollback session usability** — the fallback's re-count (SELECT), `mark_deleted` (UPDATE), and
  `_raise_for_lost_race`'s re-read (SELECT) all execute successfully on the rolled-back session against
  real PostgreSQL 16.
- **Everything round 1 cleared** — two-branch ordering, conditional writes, worker pre-flight guard,
  list filter (count and page share `conditions`), `is_deleted` immutability, FR-016/FR-019, MCP tool
  count, route registration order, migration single-headedness, and the constitution checks
  (`lib/` → no `src.api`/`src.worker` imports, no `print()`, no `assert` in `src/`, all scheduler calls via
  `asyncio.to_thread`, full type hints, Google-style docstrings, no raw SQL, no `.env`-family reads) —
  re-confirmed unchanged by this round's edits.

## Intent alignment

With M1 fixed, **FR-018 / SC-008 are now met for every terminal outcome** of a delete attempt: 404,
409, 502, 204-hard and 204-soft each emit a distinct event, and the observability contract
(`data-model.md:166-193`) enumerates all of them. Two residual traceability gaps are edge-only and
captured as **I3** (an unguarded `await` inside the fallback) and **I4** (409 not distinguishable
between the two states that produce it).

**FR-015 / FR-002 boundary** (round-1 m3) is now resolved in code — the concurrency loser gets 404 when
the row is gone and 409 when it was soft-deleted, matching `documentation/api_contract.md:196-198`. The
remaining divergence is in the opposite direction from round 1: the **design document** now lags the
code (**I1**), not the reverse.

No other divergence from `spec.md`, `plan.md`, or `research.md` D1–D9 was found. Constitution V's
parameter limit remains exceeded on the two `list` signatures, now properly recorded in Complexity
Tracking (m6) rather than silently ignored.

# Code Review Report (Round 3, final) — 035-schedule-delete

**Reviewed**: uncommitted working-tree changes on branch `035-schedule-delete`
**Base commit**: `53d74e1` (`development`)
**Scope**: `git diff HEAD` + untracked files (`migrations/versions/20260810_1200_e5f3c4d6a7b8_add_is_deleted_to_schedules.py`,
`tests/integration/test_schedule_delete.py`, `tests/unit/migrations/test_add_is_deleted.py`, `specs/035-schedule-delete/`)
**Prior rounds**: `code-review-report-round1.md` (M1, m1–m7, N1–N7), `code-review-report-round2.md` (I1–I4, N-new-1…4)
**Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `research.md`, `CLAUDE.md`
**Date**: 2026-08-10

## Verdict

**Approve — the feature is clean and ready to merge.**

All four round-2 findings (I1–I4) and the round-2 nit (N-new-1) are genuinely fixed, each with a test
that actually exercises the fixed behaviour rather than passing incidentally. I re-verified the two
tests round 2 called out as vacuous and they now assert against correctly-shaped fixtures. I found
**no new defects** in the three files touched since round 2, and I confirmed empirically against real
PostgreSQL that the riskiest new code path — `_raise_for_lost_race` reading an ORM attribute on a
post-rollback session — behaves correctly on both branches.

Nothing from rounds 1–2 regressed: only `src/lib/services/schedule_service.py`,
`tests/unit/lib/services/test_schedule_service.py`, `tests/integration/test_schedule_delete.py` and
`specs/035-schedule-delete/data-model.md` have mtimes after round 2's report (14:22); every other file
in the change set is byte-identical to what round 2 verified.

Two purely cosmetic items remain open (`plan.md:93` wording, a redundant re-test of one condition).
Neither is a defect and neither should block the merge.

### Gates re-run (this reviewer, not inherited)

| Gate | Result |
|------|--------|
| `uv run pytest` with `TEST_DB_URL` → throwaway PostgreSQL 16.14 container | **982 passed, 0 skipped, 0 failed** (47.5s) |
| `uv run ruff check src tests` | All checks passed |
| `uv run ruff format --check src tests` | 161 files already formatted |
| `uv run mypy src` | Success, no issues in 80 source files |

Container: `postgres:16` as `pg-review-035r3` on port 55437, **removed after the run** (verified: no
matching container remains). No source or test file was modified; `git status` is identical to the
pre-review snapshot.

> Housekeeping note, not a finding: a container `speckit-pg-035` (`postgres:16-alpine`, port 55435) from
> the implementation session is still running. It is not mine and I left it alone, but it should be
> cleaned up.

---

## Round-2 finding verification

| ID | Status | Evidence |
|----|--------|----------|
| **I1** — `data-model.md` §3 pseudo-code contradicted the code | **FIXED** | §3 now matches the implementation on both points. Step 4 (`data-model.md:107-111`) opens with `scheduler.state is STATE_STOPPED → raise JobRemovalError (502)` before `remove_job`, with the rationale expanded at `:139-146`. The lost-race resolution is no longer inlined as "409 on both branches": step 6 (`:118`) and `_soft_delete` (`:124`) both delegate to a new `_raise_for_lost_race` block (`:127-133`) that spells out all three outcomes — absent → 404, present+flagged → 409, present+unflagged → warning + 409. `:148-150` explains why a lost race is not always 409. The pseudo-code now leads the reader to the same conclusions as the code, closing the "design is the trap" failure mode M1 exposed. |
| **I2** — `_raise_for_lost_race` inferred 409 from row presence | **FIXED** | `schedule_service.py:238-244` now unpacks the row and classifies on the flag: `reason = "deleted_concurrently" if schedule.is_deleted else "conditional_write_matched_no_row"`, warning on the unexpected state, info on the ordinary one, and raising 409 either way ("never report success"). The docstring (`:229-232`) records why the unflagged case is impossible-but-handled. The vacuous test round 2 flagged is fixed twice over: `test_lost_race_on_soft_branch_raises_already_deleted` (`tests/unit/lib/services/test_schedule_service.py:1214-1228`) now feeds the re-read `make_deleted_schedule()` (`is_deleted = True`) and asserts exactly one `schedule_delete_conflict` with `reason == "deleted_concurrently"`; the sibling hard-branch test (`:1048-1060`) likewise builds a genuinely flagged re-read row. The formerly-untested inference is now its own test: `test_unflagged_row_after_failed_conditional_write_is_flagged_as_unexpected` (`:1242-1256`) asserts `reason == "conditional_write_matched_no_row"` **and** `log_level == "warning"`. A fixture change alone would have made the old test pass; instead the branch it left uncovered now has dedicated coverage. |
| **I3** — race-fallback re-count outside the `persist_failed` guard | **FIXED** | `schedule_service.py:324-329` wraps the re-count in `try/except Exception`, logging `schedule_delete_persist_failed` with the count exception's text before re-raising, with a comment stating why this window is the one that most needs a log. Pinned by `test_fallback_recount_failure_is_logged_as_persist_failed` (`test_schedule_service.py:1259-1274`), which drives `count_for_schedule` with `side_effect=[0, RuntimeError("db down")]` and asserts both the event and `mark_deleted.assert_not_awaited()` — so it proves the log comes from the count, not from a later `mark_deleted`. Documented at `data-model.md:114-115`. The pre-existing analogue at `:304` (initial count before `schedule_delete_start`) is unchanged, which is correct: nothing has been mutated at that point, so there is no inconsistent state for an operator to reconcile. |
| **I4** — `schedule_delete_conflict` identical from two different states | **FIXED** | The three producing states are now distinguishable. Pre-write guard: `schedule_service.py:298-302`, `reason="already_deleted"`, plus a comment contrasting it with the lost race. Lost race: `:239-243`, `reason="deleted_concurrently"` (info) or `"conditional_write_matched_no_row"` (warning). All three values are enumerated in the observability contract at `data-model.md:210`, each annotated with the operational state it denotes. Both new reasons are asserted by tests (`:1228`, `:1255`); the pre-write value is pinned indirectly by `test_delete_raises_already_deleted` (`:1327-1337`), which asserts no scheduler call and no write. |
| **N-new-1** (nit) — integration test stubbed both counts | **FIXED, and better than suggested** | `tests/integration/test_schedule_delete.py:262-281` captures the bound real method (`real_count = run_repo.count_for_schedule`) *before* patching, then installs `stale_then_real`, which returns a stale `0` on the first call and delegates to the real query afterwards. The assertion `observed == [0, 1]` is stronger than the suggested `run_count == 1` log check: it proves the second value came from a real post-rollback `SELECT` against PostgreSQL rather than from a canned mock. The docstring (`:267-271`) states the extra guarantee being bought (the session is usable for *reads* again, not just the UPDATE). The log-field guarantee is separately covered by the unit test at `:1175-1192`. This test executed in my run — 0 skips with `TEST_DB_URL` set. |
| **N-new-2** (nit) — `plan.md:93` still claims no violations | **OPEN** (cosmetic) | `specs/035-schedule-delete/plan.md:93` still reads "**Result**: PASS — no violations. Complexity Tracking not required." while `:184-189` carries two tracked violations, and the Principle V row (`:91`) still mentions only `__init__`'s 3 arguments. Doc wording only; no code or contract impact. |
| **N-new-3** (nit) — `error=str(exc)` embeds SQL + bound params | **OPEN** (cosmetic, deliberate) | `schedule_service.py:322` still logs the full `IntegrityError` text. Log-only, no secrets, correctly kept out of the 502 body. Round 2 filed it as "consider"; leaving it is a defensible call given it aids FK misattribution diagnosis (see the `delete_owned` docstring at `schedule_repository.py:88-91`, which explicitly relies on callers logging the original text). |
| **N-new-4** (nit) — `_register_job` has no `STATE_STOPPED` counterpart | **OPEN** (out of scope, as recorded) | Unchanged and unreachable today; correctly out of scope for a delete feature. |
| **N6** (round 1) — `TEST_DB_URL` not wired into CI | **Still deferred** | Unchanged, and still the right call for a feature branch. My run confirms the coverage exists and passes when the variable is set (982 passed / 0 skipped), so the only exposure is CI configuration. Worth a tracked follow-up ticket. |

---

## New defects introduced by the round-3 fixes

**None found.** Detail of what I checked and how:

### `_raise_for_lost_race` — post-rollback attribute read (the main new risk)

The I2 fix makes the helper read `schedule.is_deleted` on a re-read row. When the helper is reached via
the `IntegrityError` fallback (`schedule_service.py:330` → `_soft_delete:259`), that read happens on a
session whose identity map was expired by `delete_owned`'s `session.rollback()`
(`schedule_repository.py:103`) — precisely the hazard round 1 filed as N7 and round 2 flagged as
"latent risk if a future edit reads attributes". The round-3 edit *is* that future edit, so I probed it
rather than reasoning about it. From a scratchpad script against real PostgreSQL 16 (no repo file
touched), forcing the first count to a stale `0` with a genuine run present and making `mark_deleted`
report a lost race:

```
flip_the_flag=True    (winner really soft-deleted the row)
  counts            : [0, 1]
  outcome           : ScheduleAlreadyDeletedError (409)
  conflict events   : [('deleted_concurrently', 'info')]
  events            : schedule_delete_start → schedule_delete_job_removed
                      → schedule_delete_race_fallback → schedule_delete_conflict
  stale.is_deleted  : True    (attribute read on a pre-rollback ORM object)

flip_the_flag=False   (row still unflagged — the "impossible" state)
  counts            : [0, 1]
  outcome           : ScheduleAlreadyDeletedError (409)
  conflict events   : [('conditional_write_matched_no_row', 'warning')]
  stale.is_deleted  : False
```

Both branches classify correctly, at the right log level, with no `MissingGreenlet` and no
`DetachedInstanceError`: the re-read `SELECT` repopulates the expired instance before the attribute is
touched. N7 remains informational.

### Guarded re-count in `ScheduleService.delete`

`:324-329` catches `Exception`, not `BaseException`, so cancellation still propagates uncaught, and it
re-raises rather than swallowing — the caller still gets a 500 and the operator still gets the error
event. It sits *inside* the `except IntegrityError` handler, so the sibling `except Exception` at `:332`
cannot double-log it; I confirmed the event count is exactly one on this path
(`test_fallback_recount_failure_is_logged_as_persist_failed` asserts the event, and the `capture_logs`
list in the neighbouring tests shows no duplicate). Ordering is preserved: `_unregister_job` (`:307`)
still precedes every write (`:310`, `:314`, `:330`), so the 502 contract "nothing was changed" holds.
`raise` (bare) inside `except Exception as count_exc` re-raises the count exception, not the
`IntegrityError` — correct, since the count failure is the actionable one and the FK block was already
logged at `:318`.

### Reworked integration test `stale_then_real`

Reviewed for the classic closure traps and found none. `real_count` binds the bound method *before*
`run_repo.count_for_schedule` is reassigned (`:263` vs `:276`), so the delegation is not self-recursive.
The stale/real switch keys off `if not observed` — the emptiness of the list, evaluated before the
append — so the first call yields `0` and every later call delegates; the sequencing is asserted rather
than assumed (`observed == [0, 1]`, `:281`). `MagicMock()` as the scheduler is safe: `MagicMock.state`
compares unequal to `STATE_STOPPED`, so the m4 guard is passed rather than accidentally tripped, and
`remove_job` is a no-op mock. The `finally` block rolls back before `_cleanup` (`:289-290`), so the
teardown cannot inherit an aborted transaction, and it deletes runs before the schedule, respecting the
FK. Test isolation is intact — the id is UUID-suffixed and the patch is on a locally constructed
repository instance, so nothing leaks into other tests.

### Round-2 clean findings re-confirmed

- **Exception-path integrity** — `:335-337` remains outside the `try`, so `_raise_for_lost_race`'s
  exceptions still cannot be swallowed by `except Exception` at `:332`; `_soft_delete` raising from
  inside the `except IntegrityError` handler is likewise not caught by its sibling.
- **`NoReturn` contract** — `_raise_for_lost_race` still raises on every branch, so neither
  `schedule_deleted` (`:337`) nor `schedule_marked_deleted` (`:260`) is reachable after a lost race.
  `mypy` clean.
- **Endpoint mapping unchanged** — `src/api/v1/endpoints/schedules.py:248-261`: 404 / 409 / 502 with the
  fixed 502 message; the raw scheduler text still never leaves the log (m5 holds).
- **No regression surface** — the mtime audit above shows the worker guard, repositories, models,
  schemas, migration, endpoint and all other tests are unchanged since round 2's verification.

---

## Minor suggestions (optional polish, non-blocking)

- `schedule_service.py:239-243` computes `reason` from `schedule.is_deleted` and then re-tests the same
  condition to pick the log level. Correct, but the condition is evaluated twice and the reader has to
  match `reason` to the branch mentally. A single branch reads better:
  ```python
  if schedule.is_deleted:
      logger.info("schedule_delete_conflict", schedule_id=schedule_id, reason="deleted_concurrently")
  else:
      logger.warning(
          "schedule_delete_conflict", schedule_id=schedule_id, reason="conditional_write_matched_no_row"
      )
  raise ScheduleAlreadyDeletedError(schedule_id)
  ```
- `plan.md:93` — see **N-new-2**; reword to point at the Complexity Tracking table.
- The `conditional_write_matched_no_row` branch is covered on the soft path only
  (`test_schedule_service.py:1242`). Since both `rowcount == 0` sites share the helper, that is
  sufficient; no second test needed.

## Intent alignment

No divergence from `spec.md`, `plan.md`, `research.md` D1–D9, or `documentation/api_contract.md`
remains. The round-2 divergence ran in the opposite direction from round 1 — the design document lagged
the code — and I1's fix closes it: `data-model.md` §3 and the §4 observability table now describe
exactly what ships, including the stopped-scheduler rejection, the three-way lost-race resolution, the
guarded re-count, and all three `schedule_delete_conflict` reasons.

**FR-018 / SC-008 are fully met.** Every terminal outcome of a delete attempt emits a distinct,
attributable event — 404 (two reasons), 409 (three reasons across two operational states), 502
(including `scheduler_stopped`), 204-hard, 204-soft — and the two residual traceability gaps round 2
identified (I3, I4) are closed. The only remaining unguarded `await` before a delete event is the
initial count at `:304`, which precedes every mutation and so has no inconsistent state to report.

**FR-015 / FR-002** boundary holds: the concurrency loser gets 404 when the row is gone and 409 when it
was soft-deleted, matching `documentation/api_contract.md:196-198`, and this is now proven on a real
session rather than only against mocks.

Constitution V's parameter limit remains exceeded on the two pre-existing `list` signatures, properly
recorded in Complexity Tracking (`plan.md:184-189`) with the refactor deferred to its own change; only
the summary line at `:93` still needs rewording.

## Checklist pass

Passed with no findings: correctness and logic (all eight reachable `delete` outcomes traced, plus the
FK fallback and lost-race sub-branches), async correctness (every scheduler call via
`asyncio.to_thread`; no blocking I/O on the loop), exception handling (custom hierarchy from
`src/lib/exceptions/errors.py`, no bare `except:`, no `assert` in `src/`), type safety (full annotations,
`NoReturn` used correctly, `mypy` clean on 80 files), style (`ruff check` and `ruff format --check`
clean, ≤120 cols, Google-style docstrings, no `print()`), architecture (business logic in `services/`,
data access in `repositories/`, no raw SQL in production code, DI via `Depends()`, `lib/` imports
nothing from `api/` or `worker/`), database and ORM (conditional single-statement writes, Alembic
migration present and single-headed), project structure and test mirroring, testing (982 tests pass with
a real database; new branches individually pinned), and security (no secrets or payload content logged,
no `.env`-family reads, no scheduler diagnostics in client responses).

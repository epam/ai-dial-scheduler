# Code Review Report (Round 2) — Feature 009: Schedule Run Tracking

- **spec-dir**: `specs/009-schedule-run-tracking/`
- **Date**: 2026-06-11
- **Run mode**: Verification (second pass over round-1 findings) + targeted regression scan
- **Branch**: `009-schedule-run-tracking`
- **Artifacts consulted**: `spec.md`, `plan.md`, `code-review-1.md`

## Summary

**Approved with one minor convention fix.** All four actionable round-1 findings (I1, I2, I3,
M1) are correctly and completely addressed in the code under review. Quality gates are green
(`ruff check` clean, `ruff format --check` clean, `mypy src` 50 files no issues, 188 unit tests
pass). No regressions were introduced by the fixes. One new minor finding (I4): a third
feature-009 test file (`test_schedule_run_repository.py`) still imports from `unittest.mock`
directly — the same convention I2 addressed; round 1 missed it (and even mis-stated that it used
the `mocker` fixture). This does not block merge.

---

## Verification of Round-1 Findings

### I1 — Shutdown drain ordering — Status: **Resolved**

**File**: `src/worker/app.py:89-96`

The `finally` block now matches the round-1 suggested fix exactly:

```python
finally:
    poller.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await poller
    scheduler.shutdown(wait=True)        # no further events after this returns
    await asyncio.sleep(0)               # let shutdown-time listener tasks start
    await asyncio.gather(*tracker, return_exceptions=True)
    logger.info("Worker stopped.")
```

`scheduler.shutdown(wait=True)` now runs first, so all in-flight jobs complete and dispatch their
`EVENT_JOB_EXECUTED` / `EVENT_JOB_ERROR` events (which `create_task` and add to `tracker`) before
the drain. The `await asyncio.sleep(0)` yields the loop so those just-scheduled listener tasks are
admitted to the task set before `gather(*tracker, ...)` snapshots and awaits them. This closes the
research.md Decision 8 / Constitution I gap where terminal writes scheduled during the shutdown
window could be lost. Resolution is correct and complete.

### I2 — `unittest.mock` in tests — Status: **Resolved** (for the two cited files)

**Files**: `tests/unit/worker/test_listeners.py`, `tests/integration/test_schedule_run_lifecycle.py`

Both cited files now use the `pytest-mock` `mocker` fixture exclusively — `mocker.AsyncMock()`,
`mocker.MagicMock()`, `mocker.patch(...)` — and the `from unittest.mock import ...` line is gone
from both. Verified by a repository-wide grep: neither file matches `unittest.mock`. Resolution is
correct and complete for the scope round 1 defined.

(See I4 below: a third feature-009 file that round 1 did not flag has the same issue.)

### I3 — Resilient terminal commit with correlated failure log — Status: **Resolved**

**File**: `src/worker/listeners.py:23-55`

Both `_record_success` and `_record_error` now wrap the `Database().sessionmaker()` /
repository call in `try/except Exception`, and on failure emit a correlated `run_persist_failed`
event carrying `run_id`, `schedule_id`, and `error`, then `return` (so the generic
`listener_task_failed` path is no longer the only signal). The happy/`None` branches are preserved
unchanged. Two new tests assert the correlated event and `run_id`:

- `test_logs_run_persist_failed_on_commit_error` in `TestOnJobExecutedSuccess`
  (`test_listeners.py:121-139`)
- `test_logs_run_persist_failed_on_commit_error` in `TestOnJobError` (`test_listeners.py:265-284`)

Both assert `log.get("event") == "run_persist_failed" and log.get("run_id") == RUN_ID`. The
worker's no-raise-to-APScheduler guarantee still holds (the exception is swallowed inside the task).
This improves observability per SC-002/SC-004. Resolution is correct and complete.

### M1 — Redundant `except` tuple in REST executor — Status: **Resolved**

**File**: `src/worker/executors/rest.py:33`

Now reads `except ValueError:` alone. Since `json.JSONDecodeError` subclasses `ValueError`, this
catches the same cases without depending on PEP 758 parenthesis-free tuple syntax. Correct and
complete.

### M2 / M3 / M4 — Status: **Acknowledged, no change required**

Consistent with the round-1 disposition. M2 (response body persisted) is a contract-conformant
data-handling note. M3 (no unit test for the drain path) remains a coverage gap, but the
integration tests (`test_schedule_run_lifecycle.py`) exercise the real-scheduler success and error
paths end-to-end and drain `tracker`, which substantially covers the listener→persist→terminal
flow; a dedicated drain-ordering test is still nice-to-have. M4 (str→UUID conversion) is unchanged
and safe in practice.

---

## New Findings

### I4 — A third feature-009 test file still imports from `unittest.mock` — Status: **Open**

**File**: `tests/unit/lib/repositories/test_schedule_run_repository.py:3`

```python
from unittest.mock import AsyncMock, MagicMock
```

This is a **NEW** file created for feature 009 (plan.md line 130: `test_schedule_run_repository.py
# NEW`), so it is squarely in scope. It is the same CLAUDE.md "Testing" convention violation that
I2 addressed for the other two files — `AsyncMock()` / `MagicMock()` are constructed directly from
`unittest.mock` instead of via the `mocker` fixture. Round 1 missed this file; in fact code-review-1
(I2) incorrectly stated this test "correctly use[s] the `mocker` fixture", which is not the case.

Not a regression from the round-1 fixes, and functionally harmless (`mypy`, `ruff`, and the 188
unit tests all pass), but it leaves the feature's test suite internally inconsistent: two of three
new mock-using test files follow the convention and one does not.

**Suggested fix**: add `mocker` as a parameter to the affected tests/fixtures and replace direct
`AsyncMock()` / `MagicMock()` construction with `mocker.AsyncMock()` / `mocker.MagicMock()`, then
drop the `unittest.mock` import — exactly as was done for `test_listeners.py`. The `mock_session`
fixture at lines 26-30 is the main site to convert.

Note: `test_schedule_repository.py`, `test_schedule_service.py`, `test_schedules.py`, and
`test_schedules_api.py` also import `unittest.mock`, but those are pre-existing files from earlier
features and are out of scope for this review.

---

## Regression Check

No new issues were introduced by the I1/I2/I3/M1 fixes:

- **I1 fix** — The reordering is purely within the `finally` block; `scheduler.shutdown(wait=True)`
  is still the same synchronous call, and the added `await asyncio.sleep(0)` is a single
  no-argument yield with no side effects. No new blocking call, no changed control flow on the
  happy path.
- **I3 fix** — The `try/except` is scoped tightly around the session/repository call; the `None`
  and success branches are unchanged, and the `except` re-raises nothing (it logs and returns), so
  it cannot newly crash the listener task. The `Exception` catch is broad but appropriate here
  (best-effort persistence that must never escape the task), and it logs `str(persist_exc)` only —
  no secret exposure.
- **M1 fix** — Narrowing two exception types to their common base `ValueError` does not change
  runtime behavior.
- **I2 fix** — Test-only mechanical substitution; behavior assertions unchanged.

---

## Intent Alignment

The round-1 divergence (I1, the only material gap from research.md Decision 8 / Constitution I) is
now closed. With the drain ordering corrected, the implementation fully matches the stated design
intent: lifecycle persistence, heartbeat skip, best-effort start persistence, correlated
discrepancy/failure logging, typed cross-boundary objects, no-globals tracker, and shutdown
draining. No remaining functional divergence from `spec.md` / `plan.md`. I4 is a test-convention
inconsistency, not an intent divergence.

## Checklist Pass

- **Correctness & Logic** — Pass. I1 drain ordering corrected; no bare `except`, no `assert` in
  production; I3 `try/except` is correctly scoped and non-escaping.
- **Type Safety** — Pass. `mypy src` clean (50 files).
- **Code Style & Conventions** — Pass for production code (`ruff check` / `ruff format --check`
  clean). One test-convention gap remains (I4: `unittest.mock` in `test_schedule_run_repository.py`).
- **Architecture & Design** — Pass. No structural changes from the fixes.
- **Database & ORM** — Pass. Repository unchanged and correct; `mark_error` wraps reason as
  `{"error_text": ...}`, matching the integration test.
- **Project Structure** — Pass.
- **Testing** — Pass with one convention gap (I4). Two new `run_persist_failed` tests added for I3;
  integration lifecycle tests cover success and error end-to-end. 188 unit tests pass.
- **Security** — Pass. New `run_persist_failed` logs carry `run_id` / `schedule_id` / `str(error)`
  only; no secrets logged or persisted.

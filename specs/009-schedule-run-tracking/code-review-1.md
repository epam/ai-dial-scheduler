# Code Review Report — Feature 009: Schedule Run Tracking

- **spec-dir**: `specs/009-schedule-run-tracking/`
- **Date**: 2026-06-11
- **Run mode**: Fresh Review
- **Branch**: `009-schedule-run-tracking`
- **Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `research.md`,
  `contracts/run-listeners.md`, `contracts/run-outcome.md`, `contracts/schedule-run-repository.md`

## Summary

**Approved with minor suggestions.** The implementation is faithful to the spec and the
contracts: lifecycle persistence (in_progress → success/error), heartbeat skip, best-effort
start persistence, discrepancy logging, typed cross-boundary objects, no-globals tracker, and
shutdown draining are all present and exercised by tests. No correctness bugs that change
behavior were found. The findings below are one genuine reliability hazard (shutdown drain
ordering), a convention violation in the tests (`unittest.mock` instead of `pytest-mock`), and
a handful of polish items. None block merge on their own, but I1 (drain ordering) is worth
addressing before relying on this in production.

---

## Critical Issues

_None._

---

## Improvements

### I1 — Shutdown drain happens before scheduler shutdown, so writes scheduled during shutdown can be lost — Status: Open

**File**: `src/worker/app.py:89-95`

```python
finally:
    poller.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await poller
    await asyncio.gather(*tracker, return_exceptions=True)   # drain
    scheduler.shutdown(wait=True)                            # then shut down
    logger.info("Worker stopped.")
```

The tracker is drained **before** `scheduler.shutdown(wait=True)`. But `shutdown(wait=True)`
blocks while in-flight jobs finish, and those completions dispatch `EVENT_JOB_EXECUTED` /
`EVENT_JOB_ERROR`, whose listeners call `create_task(...)` and add **new** tasks to `tracker`.
Those tasks are created *after* the `gather` already returned, so they are never awaited — the
process can exit (`run_worker` → `asyncio.run` returns) with terminal writes still pending. This
directly undercuts research.md Decision 8 / plan Constitution I ("pending persistence tasks are
drained on shutdown to prevent data loss"): a run that finishes during the shutdown window can
be lost exactly as the design set out to prevent.

Note also `scheduler.shutdown(wait=True)` is a **synchronous** call on `AsyncIOScheduler`; it
does not yield to the loop, so any tasks it causes to be scheduled only run after it returns —
by which point nothing awaits them.

**Suggested fix**: shut the scheduler down first (so no further events will be dispatched), then
drain. Because `shutdown(wait=True)` is sync and won't let scheduled tasks run, follow it with a
yield so the just-scheduled listener tasks actually start before the gather:

```python
finally:
    poller.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await poller
    scheduler.shutdown(wait=True)        # no further events after this returns
    await asyncio.sleep(0)               # let listener-scheduled tasks start
    await asyncio.gather(*tracker, return_exceptions=True)
    logger.info("Worker stopped.")
```

The existing `test_worker_app.py` tests do not cover the drain at all (the scheduler is fully
mocked and `stop_event` is pre-set), so this regression is invisible to the current suite — see
M3.

### I2 — Tests use `unittest.mock` directly instead of `pytest-mock` — Status: Open

**Files**: `tests/unit/worker/test_listeners.py:3`,
`tests/integration/test_schedule_run_lifecycle.py:5`

```python
from unittest.mock import AsyncMock, MagicMock
```

CLAUDE.md ("Testing") and the project conventions state: *"Use `pytest-mock` for mocking."* The
repository test (`test_schedule_run_repository.py`) and `test_jobs.py` correctly use the
`mocker` fixture (`mocker.AsyncMock()`, `mocker.patch(...)`), but `test_listeners.py` and the
integration test import `AsyncMock`/`MagicMock` from `unittest.mock` directly. The `mocker`
fixture is already a parameter in these tests, so the dependency is available.

**Suggested fix**: replace direct `AsyncMock()`/`MagicMock()` construction with
`mocker.AsyncMock()` / `mocker.MagicMock()` and drop the `unittest.mock` import, matching the
sibling test modules.

### I3 — `mark_success` / `mark_error` are not resilient to a failing terminal commit — Status: Open

**File**: `src/lib/repositories/schedule_run_repository.py:30-48`

If `self._session.commit()` raises inside `mark_success` / `mark_error` (e.g. transient DB
error), the exception propagates out of the listener-scheduled task. It is *caught* — the
`_on_task_done` callback in `listeners.py:17-21` logs `listener_task_failed` — so the worker
does not crash and the design's "listener never raises out to APScheduler" guarantee holds.
However, the failure is logged only as a generic `listener_task_failed` with `error=str(exc)`,
not as a run-correlated event carrying `run_id` / `schedule_id`. SC-002/SC-004 expect every run
to reach a terminal status; a swallowed commit failure leaves the row in `in_progress` with only
an uncorrelated log line, which is hard to triage.

This is acceptable as best-effort (matches spec Assumptions tone), but consider wrapping the
terminal write in the listener's `_record_success` / `_record_error` with a `try/except` that
logs a correlated `run_persist_failed` event (`run_id`, `schedule_id`, `error`) so operators can
tie the failure to the specific run. Low priority; current behavior is safe, just less
observable.

---

## Minor Suggestions

### M1 — REST executor relies on Python 3.14's parenthesis-free `except` tuple

**File**: `src/worker/executors/rest.py:33`

```python
except json.JSONDecodeError, ValueError:
```

I verified this is **not** a bug: under Python 3.14 (PEP 758) this parses as
`except (json.JSONDecodeError, ValueError):` — a tuple of two exception types — and at runtime
catches both. It compiles, `ast` confirms a `Tuple` of exception types, and ruff/mypy pass.
However, `json.JSONDecodeError` is already a subclass of `ValueError`, so catching both is
redundant — `except ValueError:` alone is sufficient. For readability and to avoid depending on
a brand-new syntax feature, prefer either `except ValueError:` or the explicit
`except (json.JSONDecodeError, ValueError):`.

### M2 — REST executor logs and persists the full response body

**File**: `src/worker/executors/rest.py:36-37`

`logger.info("run_result", ..., result=response.text)` logs the entire response body, and the
returned `{"body": ...}` is stored verbatim in `schedule_runs.result`. This is per the
run-outcome contract (result shapes carry response data, not request auth headers, so FR-012's
"no credentials in results" is met for the *request* side). Be aware that if a downstream
endpoint echoes a secret in its *response*, that secret would be both logged and persisted. Not
a defect against the current contract; flagging as a data-handling consideration only.

### M3 — No test covers the shutdown drain path

**File**: `tests/unit/worker/test_worker_app.py:73-114`

`TestListenerRegistration` asserts both listeners are registered but fully mocks the scheduler
and pre-sets `stop_event`, so the `await asyncio.gather(*tracker, ...)` drain and its ordering
(I1) are never exercised. Consider a test that, with a real `AsyncIOScheduler`, registers a job
that completes during shutdown and asserts the terminal row is written — or at minimum a unit
test asserting tracker tasks created during `shutdown` are awaited. This would catch I1 and
guard the Constitution I guarantee.

### M4 — `RunContext.run_id` is `str` but the PK is `UUID`; conversion is unvalidated

**File**: `src/lib/repositories/schedule_run_repository.py:18,31,41`

`uuid.UUID(run.run_id)` / `uuid.UUID(run_id)` will raise `ValueError` if a non-UUID `run_id` ever
reaches the repository. Today `run_id` is always `str(uuid4())` from `jobs.py:19`, so this is
safe in practice. No action needed unless `run_id` could ever originate elsewhere; mentioning for
completeness since the str↔UUID boundary is implicit.

---

## Intent Alignment

The implementation matches the spec and contracts well:

- **FR-001 / US1** — `execute_scheduled_job` creates the `in_progress` row before validation
  (`jobs.py:26-31`), with `end_time=None`, `result=None`, `schedule_id`, and a JSON-safe
  `schedule_snapshot` built from kwargs (enum values coerced via `v.value`). Matches data-model.
- **FR-002 / SC-005** — Heartbeat returns `None`; `on_job_executed` skips non-`RunOutcome`
  retvals and logs `run_persist_skip` (`listeners.py:39-42`). Verified by
  `test_skips_non_run_outcome_retval_and_logs_persist_skip`.
- **FR-003 / FR-005 / US2** — Success returns `RunOutcome(run_id, schedule_id, result)`;
  `mark_success` sets status, tz-aware `end_time`, and `result`. Lookup is by `run_id` PK
  (FR-007), not inference.
- **FR-004 / FR-006 / FR-008 / US3** — Failures (including `ValidationError`) are wrapped into
  `ScheduleRunError(run_id, schedule_id)` (`jobs.py:40-45`); `on_job_error` records via
  `mark_error` with `{"error_text": str(exc)}`. Validation-before-start failures are still
  recorded because the in_progress row is created before validation.
- **FR-009 / SC-006** — Every lifecycle action logs: `run_start`, `run_created`,
  `run_create_failed`, `run_recorded_success`, `run_failed`, `run_recorded_error`,
  `run_record_missing`, `run_persist_skip`, `job_error`. Matches the run-listeners log table.
- **FR-010** — `mark_success`/`mark_error` return `None` for a missing row; listeners log
  `run_record_missing` rather than raising. Verified by tests.
- **FR-011** — Single row keyed by `run_id` (= PK `id`); start and terminal refer to the same row.
- **FR-012** — Result shapes carry response data only; API key is injected into request headers,
  not into results (see M2 caveat about response-side echoes).
- **Constitution / no-globals** — Tracker is a closure-captured `set` built in `_run`
  (`app.py:63`), passed to `build_run_listeners`; no module-level mutable state. Listeners are
  synchronous and only `create_task` (`listeners.py:43,53`), never block — matches research.md
  Decision 1.

**Divergence**: The only material gap from stated intent is **I1** — the design (research.md
Decision 8) promises pending writes are drained on shutdown, but the current ordering can drop
writes scheduled *during* `scheduler.shutdown(wait=True)`.

---

## Checklist Pass

- **Correctness & Logic** — Pass. Lifecycle transitions, heartbeat skip, validation-before-start
  ordering, and discrepancy handling are correct. No bare `except`, no `assert` in production
  code. (Caveat: I1 reliability ordering.)
- **Type Safety** — Pass. Complete type hints; `Executor` Protocol returns `dict[str, Any]`;
  Pydantic v2 models (`RunStart`, `RunOutcome`) are well-formed; `mypy src` clean.
- **Code Style & Conventions** — Pass. `ruff check` and `ruff format --check` clean; line length
  ok; logger used (no `print`); imports ordered. (Minor: M1 syntax-style preference.)
- **Architecture & Design** — Pass. Repository pattern, business logic in worker/services layer,
  `lib/` has no inward dependency on `worker/`, functions ≤3 params via Pydantic models, session
  ownership in callers per contract.
- **Database & ORM** — Pass. SQLModel/SQLAlchemy only, no raw SQL; reuses existing table/migration
  as planned; tz-aware `end_time`; `session.get` PK lookup; each method commits its own tx.
- **Project Structure** — Pass. New files land in `src/lib/repositories/`, `src/lib/schemas/`,
  `src/worker/`; tests mirror `src/` under `tests/unit/...` and `tests/integration/`.
- **Testing** — Pass with one convention issue (I2: `unittest.mock` in two files) and one
  coverage gap (M3: shutdown drain untested). Repository, listeners, jobs, executors, and an
  end-to-end lifecycle integration test are all present; happy and error paths covered.
- **Security** — Pass. API key injected via secret value, never logged (asserted by
  `test_dial_api_key_not_in_logs`); results carry response data only (M2 is an awareness note,
  not a violation).

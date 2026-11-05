# Code Review 2 — APScheduler Executor Worker

**Spec dir**: `specs/008-apscheduler-executor-worker/`
**Date**: 2026-06-11
**Mode**: Fresh independent review (round 2, verifying round-1 fixes)
**Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `contracts/executor-interface.md`, `code-review-1.md`

## Summary

**Approved with minor suggestions.** All 12 findings from review 1 are resolved correctly: the signal handler is now loop-safe with a Windows fallback, the poller task is retained/cancelled/awaited on shutdown, contextvars are cleared in `finally` and at heartbeat entry, the SSE non-2xx path is explicit, chat streaming uses `create(stream=True)` (raw chunks), `schedule_id`/`run_id` flow explicitly through `RunContext`, telemetry is imported directly with a logged failure, `on_job_error` is typed, the duplicate per-call timeout is gone, and the redundant `Accept` injection / `str()` coercions are removed. No new critical or major defects were introduced. Two small leftovers from the fixes (an orphaned constant and a still-unused model) plus a doc/code drift remain.

---

## Verification of Review-1 Findings

| # | Title | Sev (R1) | Status |
|---|-------|----------|--------|
| 1 | Signal handler not async-safe | major | **Resolved** — `app.py:65-72` uses `loop.add_signal_handler` with `signal.signal → call_soon_threadsafe` fallback on `NotImplementedError`. |
| 2 | Poller never cancelled/awaited | major | **Resolved** — `app.py:98-104` retains `poller`, cancels and awaits it in `finally` (suppressing `CancelledError`), then shuts the scheduler down. Inner loop uses `asyncio.wait_for(stop_event.wait(), timeout=...)` for prompt stop. |
| 3 | contextvars leak after run | major | **Resolved** — `jobs.py:33-34` clears in `finally`; `heartbeat()` clears at entry (`jobs.py:38`). Tests `test_contextvars_cleared_after_success/failure` and `test_heartbeat_clears_contextvars` lock this in. |
| 4 | SSE non-2xx not surfaced per contract | major | **Resolved** — `sse.py:28-29` checks `event_source.response.status_code >= 400` and calls `raise_for_status()`; `except` now also catches `httpx_sse.SSEError` (`sse.py:32`). |
| 5 | Chat streaming used `.stream()` helper | major | **Resolved** — `chat_completion.py:41-46` uses `await create(**payload, stream=True)` and iterates raw chunks; non-stream path uses plain `create`. Matches contract "one record per chunk". |
| 6 | `schedule_id` recovered from contextvars w/ `""` fallback | major | **Resolved** — interface now takes `ctx: RunContext` (`base.py:8`); all three executors raise `ScheduleRunError(..., schedule_id=ctx.schedule_id, run_id=ctx.run_id)`. No ambient-state fallback remains. |
| 7 | `init_telemetry` via importlib + silent pass | minor | **Resolved** — `app.py:89-94` imports `from aidial_sdk.telemetry.init import init_telemetry` directly and logs `telemetry_init_failed` on exception. |
| 8 | `on_job_error` untyped; lost correlation | minor | **Resolved** — `app.py:35` typed `event: JobExecutionEvent`; `else` branch logs `job_id=event.job_id` (`app.py:40`). |
| 9 | Duplicate per-call timeout | minor | **Resolved** — timeout lives only on the `httpx.AsyncClient` (`app.py:74`); no per-call `timeout=` in `rest.py`/`sse.py`. |
| 10 | `RunContext`/`RunResult` unused | minor | **Partially resolved** — `RunContext` is now used throughout; `RunResult` remains defined-but-unused. See Finding A. |
| 11 | Redundant SSE `Accept` injection | minor | **Resolved** — manual `Accept` header removed; `aconnect_sse` owns it. But the constant left behind is now dead — see Finding B. |
| 12 | Redundant `str()` coercions | minor | **Resolved** — `rest.py:20`/`sse.py:23` use `properties.method.value`; `url=properties.url` passed directly (already `str`). |

---

## New / Remaining Findings

## Finding A: `RunResult` is still defined but never used
**File**: `src/lib/schemas/run.py:11-14`
**Severity**: minor
**Description**: Review-1 Finding 10 asked to either adopt or drop `RunContext`/`RunResult`. `RunContext` is now used everywhere, but `RunResult` is still imported nowhere in `src/`. The success records are emitted as ad-hoc kwargs (`logger.info("run_result", run_id=..., status=..., result=...)` in `rest.py:29`; `result=str(response)` in `chat_completion.py:49`) rather than via the "standardized success object" the data-model attributes to `RunResult`. It is dead code under YAGNI.
**Suggestion**: Either delete `RunResult` (simplest, honors YAGNI), or actually emit it from the REST and chat single-result paths (`logger.info("run_result", **RunResult(run_id=ctx.run_id, schedule_id=ctx.schedule_id, result=...).model_dump())`) so the data-model claim holds. Pick one; don't leave it half-wired.

## Finding B: `SSE_ACCEPT_HEADER` constant is now orphaned
**File**: `src/lib/constants.py:12`
**Severity**: minor
**Description**: Removing the manual `Accept` injection from `sse.py` (Finding 11 fix) left `SSE_ACCEPT_HEADER = "text/event-stream"` with no remaining references anywhere in the codebase. Dead constant.
**Suggestion**: Remove the constant. (If you want to document that the SSE transport owns `Accept`, do it in a comment in the contract, not as an unused symbol.)

## Finding C: Executor-interface contract still documents the old signature
**File**: `specs/008-apscheduler-executor-worker/contracts/executor-interface.md:9-19`
**Severity**: minor
**Description**: The implemented interface is `execute(self, ctx: RunContext, properties: ScheduleProperties)` (`base.py:8`, all executors, `data-model.md:71`), but `contracts/executor-interface.md` still shows `async def execute(self, run_id: str, properties: ScheduleProperties)` and the prose "executors receive `run_id` explicitly". This is a Contract-Driven (Constitution IV) drift — the typed contract no longer matches the code it governs. `data-model.md` was updated to mention `RunContext` for binding but its interface block (line 71) still reads `execute(run_id, properties)` as well.
**Suggestion**: Update both `contracts/executor-interface.md:9-11` and `data-model.md:71` to `execute(self, ctx: RunContext, properties)` and adjust the surrounding prose ("executors receive a `RunContext` carrying `run_id` + `schedule_id`").

## Finding D: Chat-completion client construction sits outside the executor's `try`
**File**: `src/worker/executors/chat_completion.py:27-35`
**Severity**: minor
**Description**: `_parse_deployment(...)` (which raises `ValueError` for a URL without a `deployments/<name>` segment) and `AsyncAzureOpenAI(...)` (which raises if `api_version` is `None` — permitted by `ScheduleProperties` for `chat_completion`) execute *before* the `try` on line 40. The executor itself therefore does not wrap these into `ScheduleRunError`, diverging from the executor contract ("Raises `ScheduleRunError` on any failure"). It is *not* a correctness defect end-to-end: `execute_scheduled_job` (`jobs.py:31-32`) catches any `Exception` and re-wraps it with the correct `schedule_id`/`run_id`, so FR-014/SC-002 still hold and the existing tests pass. The gap is contract-local: an executor invoked directly (e.g. in a future caller or a unit test) would surface a raw `ValueError`/openai error instead of `ScheduleRunError`.
**Suggestion**: Move `_parse_deployment` and the `AsyncAzureOpenAI(...)` construction inside the `try` block so the executor honors its own "any failure ⇒ `ScheduleRunError`" contract independently of the dispatch handler's safety net.

## Finding E: SSE failure check only covers `>= 400`, not all non-2xx
**File**: `src/worker/executors/sse.py:28-29`; contract `executor-interface.md:47`
**Severity**: minor
**Description**: The contract/spec define success as 2xx and "non-2xx before stream open ⇒ `ScheduleRunError`". The guard checks `status_code >= 400`, which omits 3xx. In practice httpx follows redirects on the shared client by default, so a 3xx rarely reaches here, and a 1xx is not a final status — so this is unlikely to fire. Still, the predicate does not literally match "non-2xx". The REST path is unaffected: `raise_for_status()` there already treats 3xx-with-no-redirect / 4xx / 5xx uniformly.
**Suggestion**: For exact contract parity, gate on `not 200 <= status_code < 300` (or rely solely on `raise_for_status()`, which raises for 4xx/5xx and is consistent with the REST executor). Low priority given default redirect handling.

---

## Intent Alignment

- **FR-004 / SC-002** (every record carries both ids): now solidly met — `RunContext` is bound into contextvars and passed explicitly to executors; failures carry both ids via `ctx`. The `""`-fallback risk from R1 Finding 6 is gone.
- **FR-010 / SC-004** (one record per chunk for chat streaming): met — `create(stream=True)` yields raw `ChatCompletionChunk`s, one `stream_chunk` log each (`chat_completion.py:45-46`).
- **FR-013** (SSE stream-accepting preference; extra_headers win on collision): `Accept` is owned by `aconnect_sse`; extra_headers still win for all other headers via `{Api-Key, **extra_headers}`. The documented `Accept`-collision exception remains a transport limitation (acceptable; note it in the contract per R1 Finding 11).
- **US3 / heartbeat "nothing else"** (SC-005): met and guarded by `test_logs_nothing_else` + `test_heartbeat_clears_contextvars`.
- **FR-002 / FR-003** (worker registers exactly one job; never enumerates task jobs): met — single heartbeat in the `local` MemoryJobStore; the `_jobstore_poller` only calls `scheduler.wakeup()`, it does not enumerate or register task jobs.
- **FR-016 / SC-007** (no persistence): met — logging only; no DB writes in the dispatch path.
- **Constitution IV** (contract-driven): the one open gap is doc drift — see Findings C.

## Checklist Pass (areas with no issues found)

- Worker lifecycle: signal handling, poller cancellation, scheduler shutdown, client close, and context teardown are all correct and ordered properly in the `finally` block (`app.py:99-108`).
- `ScheduleRunError(message, schedule_id, run_id)` unchanged and correct; error listener logs with correlation ids.
- No raw SQL; no DB read in the worker dispatch path.
- No `print()`; structured `logger` throughout; `dial_api_key` never logged (asserted per executor: `test_dial_api_key_not_in_logs`).
- No reading of `.env`/secrets in feature code.
- Type hints present on all signatures including `on_job_error` and the executor protocol; `mypy` reported clean.
- Dispatcher `TargetType → Executor` map is total over the enum and minimal (KISS/YAGNI).
- Validation reuses `ScheduleProperties` verbatim.
- Tests mirror `src/` layout, use `pytest-mock` + `structlog.testing.capture_logs`; the stream test now mocks `create(stream=True)` returning an async generator, matching the new implementation.

---

## Counts

- **Critical**: 0
- **Major**: 0
- **Minor**: 5 (A: unused `RunResult`; B: orphaned `SSE_ACCEPT_HEADER`; C: contract/data-model signature drift; D: chat client construction outside `try`; E: SSE `>=400` vs non-2xx)

**Verdict: Approved.** All 12 round-1 findings are resolved; no critical or major issues remain. The 5 minor items are polish/consistency cleanups and do not block merge.

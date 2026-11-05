# Code Review Report: Schedule Timeout & Retry (`020-schedule-timeout-retry`)

**Reviewer**: code-review-agent
**Date**: 2026-06-17
**Mode**: Fresh review
**Scope**: Diff of `020-schedule-timeout-retry` against `master`
**Artifacts consulted**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/schedule-properties.md`, `.specify/memory/constitution.md`, `CLAUDE.md`

## Summary

**Verdict: Approved with required changes.** The core retry/timeout orchestrator (`src/worker/retry.py`) is clean, correct for the main flows, well-instrumented, and well-tested. The retry loop semantics (attempt counting, backoff ordering, retryable matching, last-retry vs non-retryable logging) match the spec. However there are two correctness/reliability defects worth fixing before merge: a **timeout-passthrough gap** that directly contradicts a stated design decision (Major), and a **timeout under-enforcement** for `chat_completion` streaming plus an httpx pool-timeout edge case. There are also several Minor/Nit observability and consistency items.

---

## Critical Issues

None.

---

## Major Issues

### M1 — Executors pass `properties.timeout`, not the effective timeout, to the HTTP/SDK layer — the documented 60s-default protection does not work
- **Status**: Open
- **Files**:
  - `src/worker/executors/rest.py:27-28`
  - `src/worker/executors/sse.py:24-25`
  - `src/worker/executors/chat_completion.py:41-42`
- **Problem**: `research.md` R3 / `plan.md` decision 4 state the executors pass `effective_timeout` to the underlying HTTP/SDK call "so a schedule timeout larger than the shared client default is honoured and not pre-empted." The implementation only forwards the per-request timeout **when `properties.timeout is not None`**, and even then forwards `properties.timeout` rather than the orchestrator's `effective_timeout`. Consequences:
  - When `timeout` is unset, no per-request timeout is passed, so the shared client default (60s) applies. This is fine functionally (default == 60s), but it means the orchestrator's `effective_timeout` and the HTTP-layer timeout are computed in two different places and can drift.
  - When `timeout` **is** set to a value **larger** than the shared client's `read_timeout_seconds` (default 60s) for REST/SSE: httpx's `read` timeout (60s) still fires first for any single read that exceeds 60s. For SSE specifically, `httpx.Timeout(float(properties.timeout))` *does* override read too (single-arg form sets all four), so SSE is OK; but for REST `client.request(..., timeout=httpx.Timeout(float(properties.timeout)))` also sets all four, so REST is OK too. The real residual gap is **`pool` timeout**: a long schedule timeout does not extend connection-pool acquisition, but that is acceptable.
  - The genuinely broken case is the **mismatch of source of truth**: the orchestrator caps at `effective_timeout` via `asyncio.wait_for`, but the executor's per-request timeout is independent. They happen to coincide today only because the default equals 60s. If `worker_http.timeout_seconds` is reconfigured (env override) to anything other than 60s, an unset-`timeout` schedule will have `asyncio.wait_for(effective_timeout=<new default>)` but the HTTP client still uses its own configured timeouts — they will agree only by coincidence.
- **Why it matters (Reliability First)**: The stated invariant "schedule timeout larger than the shared client default is honoured" is only partially realized, and the two timeout layers are not derived from one value. The safest fix makes the executor receive and apply `effective_timeout`.
- **Suggested fix**: Compute `effective_timeout` once in the orchestrator and pass it to the executor (e.g., extend the `Executor.execute` signature or `RunContext` to carry the effective timeout), then in each executor always set the per-request timeout from that single value rather than gating on `properties.timeout is not None`. At minimum, document explicitly that the per-request timeout is best-effort and `asyncio.wait_for` is authoritative, and forward `effective_timeout` instead of `properties.timeout`.

### M2 — `chat_completion` streaming timeout is not fully bounded by the per-request `timeout`; relies solely on `asyncio.wait_for`
- **Status**: Open
- **File**: `src/worker/executors/chat_completion.py:43-52`
- **Problem**: For streaming responses the OpenAI `timeout=` applies to the request, but the `async for chunk in response` consumption loop runs after `create()` returns. The total stream consumption is only bounded by the orchestrator's `asyncio.wait_for`. This is acceptable (the orchestrator is authoritative), but note the same dual-source-of-truth concern as M1: the OpenAI `timeout` is `properties.timeout`, not `effective_timeout`, so an unset timeout leaves the SDK at its own default while `wait_for` uses `worker_http.timeout_seconds`. Align both on `effective_timeout` (see M1).
- **Suggested fix**: Same as M1 — thread `effective_timeout` through and always pass it.

---

## Minor Issues

### m1 — `attempt_failed` fallback branch can be reached for a retry-policy run when `status_code is None` on the last attempt (logging clarity)
- **Status**: Open
- **File**: `src/worker/retry.py:116-127`
- **Problem**: The final-failure logging chooses among `last_retry_failed`, `attempt_failed_non_retryable`, and `attempt_failed`. Consider a retry policy where the **last** attempt fails with `status_code = None` (e.g., a transport error on the final retry after earlier retryable failures). `should_retry` is False (no status code). The `last_retry_failed` branch requires `is_retry and attempt == max_attempts` — which **is** true here, so it logs `last_retry_failed`. Good. But for a single-attempt run *with* a retry policy where attempt 1 fails with `status_code=None` (transport error), none of the first two conditions match (`is_retry` is False; `status_code is None` so the non-retryable branch is skipped), and it correctly falls to `attempt_failed`. This is fine, but the `attempt_failed_non_retryable` event is **only** emitted when `status_code is not None and status_code not in retryable`. A non-retryable failure due to a *missing* status code (transport/timeout-like) is logged as the generic `attempt_failed`. FR-012/observability are satisfied for the documented cases, but operators may expect a clearer "not retryable because no status code" signal.
- **Suggested fix**: Optional — add a `reason` field (e.g., `reason="no_status_code"` vs `reason="status_not_in_retryable"`) to the failure log so the non-retry decision is explicit in all cases.

### m2 — `retry_scheduled` and `last_retry_failed` logs omit `run_id`/`schedule_id` keys explicitly (rely on contextvars)
- **Status**: Open
- **File**: `src/worker/retry.py:76-132`
- **Problem**: The orchestrator's structured logs (`attempt_start`, `retry_scheduled`, `last_retry_failed`, etc.) do not pass `schedule_id`/`run_id`. They are bound via `bind_contextvars` in `jobs.py` and merged by `merge_contextvars`, so the fields *do* appear in output. This is correct, but it makes `retry.py` logs implicitly dependent on the caller having bound contextvars — a unit test calling `execute_with_policy` directly (as `test_retry.py` does) will emit logs *without* `schedule_id`/`run_id`. Constitution II requires task ID + tenant identifier on scheduling-decision logs (enqueue/retry/discard). The retry events qualify and currently satisfy this only transitively.
- **Suggested fix**: Optional but recommended — pass `schedule_id=ctx.schedule_id, run_id=ctx.run_id` explicitly on the retry/timeout decision logs, or document the contextvar dependency.

### m3 — `attempt_failed_non_retryable` not logged when retries are simply exhausted-by-count but the failing code IS retryable (it logs `last_retry_failed`, correct) — verify intent for the "retries_remain false but retryable code" first-of-multiple case
- **Status**: Open
- **File**: `src/worker/retry.py:98-99,116-127`
- **Problem**: `should_retry = retries_remain and status_code in retryable`. On the **last** attempt, `retries_remain` is False, so even a retryable code does not retry (correct — no budget left) and falls to `last_retry_failed` (correct). No bug; calling out for confirmation that the spec's "the final attempt is the last retry and it failed" wording is satisfied. It is (FR-012). No change required.

### m4 — `timeout` typed as `int` only; spec says integer seconds, but `effective_timeout` becomes `float` — `timeout_seconds` in attempt records is `float` even for integer input
- **Status**: Open
- **File**: `src/worker/retry.py:64-65,131`; `data-model.md` AttemptRecord `timeout_seconds: int | float`
- **Problem**: `effective_timeout = float(properties.timeout) if ... else worker_http.timeout_seconds` (which is `float`). So `timeout_seconds` persisted in attempt records is always a float (e.g., `30.0`). The data-model declares `int | float`, so this is compliant, but the persisted JSON will show `30.0` rather than `30`. Cosmetic; flag only if downstream consumers compare strictly.
- **Suggested fix**: None required; acceptable per data-model.

### m5 — Backward-compat path is correct but untested for a stored snapshot lacking the new keys at the orchestrator level
- **Status**: Open
- **File**: `src/worker/jobs.py:78` → `ScheduleProperties(**kwargs)`
- **Problem**: Old schedules' `kwargs` lack `timeout`/`retry`; `ScheduleProperties` defaults both to `None`, yielding `effective_timeout = default` and `max_attempts = 1`. This is correct and matches FR-015/SC-004. The retry unit tests cover the `None` defaults indirectly (`make_properties()` with no timeout/retry). Backward compatibility is satisfied; no code change needed. Recommend an explicit integration test deserializing a pre-feature `kwargs` dict to lock SC-004.

---

## Nits

### n1 — `asyncio.TimeoutError` vs `TimeoutError`
- **File**: `src/worker/retry.py:87`
- On Python 3.11+ `asyncio.TimeoutError` is an alias of the builtin `TimeoutError`. Catching `asyncio.TimeoutError` is fine and explicit; no change needed. (Noted because Python 3.14 is the target and some linters flag the alias.)

### n2 — `retryable_errors=sorted(retryable) if retryable else None` re-sorts every attempt
- **File**: `src/worker/retry.py:82`
- Recomputed each loop iteration for the log. Negligible, but could be hoisted above the loop into a local for clarity.

### n3 — `RunOutcome` is a `@dataclass`, other "result carrier" types in the codebase are Pydantic models
- **File**: `src/worker/retry.py:14-28`
- Constitution V allows grouping data; dataclass is fine and lighter. Consistency-only observation — the codebase uses Pydantic for `RunContext`/schemas. Acceptable.

### n4 — Unreachable guard raises `RuntimeError` rather than being unreachable via typing
- **File**: `src/worker/retry.py:135-136`
- Reasonable defensive guard with `# pragma: no cover`; fine.

### n5 — `run_result` log in REST executor logs full response text
- **File**: `src/worker/executors/rest.py:48`
- Pre-existing behavior (not introduced by this feature): `logger.info("run_result", ..., result=response.text)` logs the entire response body. No credentials are added by this feature's changes, and request headers (which contain `dial_api_key`) are NOT logged. No secret leakage introduced by this branch. Flag only as a general data-volume/PII consideration for a future hardening pass; out of scope for this review.

---

## Intent Alignment

| Requirement | Status | Notes |
|-------------|--------|-------|
| FR-001/FR-003/FR-005 timeout per attempt, default fallback | Met | `effective_timeout` + `asyncio.wait_for` per attempt. See M1/M2 for the HTTP-layer passthrough mismatch. |
| FR-002/FR-006 retry with ordered backoff, count = len(backoff) | Met | `max_attempts = 1 + len(backoff)`; `backoff[attempt-1]` before retry; `asyncio.sleep` (no loop blocking). |
| FR-004 no retry when `retry` absent | Met | `max_attempts = 1`. |
| FR-007 retry only on matching status code | Met | `status_code in retryable`; non-matching → immediate fail. |
| FR-008 final failure after exhaustion | Met | Loop returns terminal `RunOutcome`. |
| FR-009/SC-006 persist all attempts | Met | `attempts[]` in `result` JSONB via `mark_success`/`mark_error`. |
| FR-010/011/012 structured logs (timeout, retry label, last-retry-failed) | Met | `attempt_start`, `retry_scheduled`, `last_retry_failed`, `attempt_failed_non_retryable`. See m1/m2 for edge clarity. |
| FR-013 validation (positive timeout, non-empty positive backoff, valid HTTP codes) | Met | `RetryPolicy` + `validate_timeout` validators; `retryable_errors` non-empty + 100–599 (tightens spec edge case per research R7, as documented). |
| FR-014 echo timeout/retry on retrieve | Met | `ScheduleResponse.properties` sources from `kwargs` verbatim. |
| FR-015/SC-004 backward compatibility | Met | Defaults to `None` → default timeout, single attempt. See m5. |
| Spec Assumption: timeouts/transport errors terminal (no status_code) | Met | Timeout → `status_code=None`, returns immediately; executors raise `ScheduleRunError` without status for `TransportError`/`SSEError`. |
| Design decision 4 (executor forwards effective timeout to HTTP/SDK) | **Partially met** | Executors forward `properties.timeout` only when set, not `effective_timeout`. See M1. |
| No DB migration | Met | Reuses JSONB columns. |
| Contract IV (typed API↔worker via `ScheduleProperties`) | Met | New fields on `ScheduleProperties`; no ad-hoc dict keys. |

---

## Checklist Pass

- **Correctness & logic (retry loop)**: PASS — attempt counting, backoff ordering, retryable matching, success short-circuit, last-retry vs non-retryable logging all correct.
- **Timeout enforcement (orchestrator)**: PASS for `asyncio.wait_for`; **CONCERN** on HTTP/SDK passthrough source-of-truth (M1/M2).
- **Timeouts/transport = terminal**: PASS.
- **Backward compatibility**: PASS.
- **Async / no event-loop blocking**: PASS (`asyncio.sleep`, `await asyncio.wait_for`).
- **No `assert` / no `print()`**: PASS.
- **Type hints / Google docstrings**: PASS (orchestrator and exception documented; executors lack docstrings but match pre-existing style).
- **Max 2–3 params / Pydantic grouping**: PASS (`_attempt_record` has 7 positional params — internal helper, borderline; consider a small dataclass, see below).
- **Observability (structured logging, persist all attempts)**: PASS (with m1/m2 polish suggestions).
- **Secret leakage in new logs**: PASS — no credentials added to logs by this branch.
- **Tests (Test-First)**: PASS — `tests/unit/worker/test_retry.py` covers success, single-attempt failure, schedule vs default timeout, terminal timeout, timeout-not-retried, retry-then-success, exhaustion + backoff order, non-retryable, missing-status, and logging assertions.

### Additional Nit — `_attempt_record` parameter count
- **File**: `src/worker/retry.py:31-49`
- 7 positional parameters exceeds the constitution's "2–3 params" guidance. It is a private helper building a dict, so the risk is low, but consider grouping into a small dataclass / building the dict at the call sites, or passing a Pydantic `AttemptRecord` model for consistency with the data-model document.

---

## Recommended actions before merge
1. **M1/M2**: Thread a single `effective_timeout` into the executors and always apply it as the per-request HTTP/SDK timeout (don't gate on `properties.timeout is not None`), so the orchestrator and HTTP layer share one source of truth and the documented "honour timeouts larger than the client default" guarantee holds under any `worker_http.timeout_seconds` value.
2. **m1/m2 (optional)**: Add an explicit non-retry `reason` and/or pass `schedule_id`/`run_id` on retry-decision logs.
3. **m5 (optional)**: Add an integration test deserializing a pre-feature `kwargs` snapshot to lock SC-004.

---

## Follow-up Review (round 2)

**Reviewer**: code-review-agent
**Date**: 2026-06-17
**Mode**: Verification of fixes for round-1 findings
**Scope**: Working-tree state of `src/worker/retry.py`, `src/worker/executors/{rest,sse,chat_completion}.py`, `src/lib/schemas/run.py`, `src/lib/exceptions/errors.py` (changes are uncommitted on `020-schedule-timeout-retry`; reviewed via current file contents, not `git diff master...HEAD`, which does not yet include them).
**Validation**: `uv run pytest` → **398 passed, 92 skipped**; `tests/unit/worker/test_retry.py` + `tests/unit/worker/executors` → **52 passed**.

### Final verdict: **APPROVED.** No Major or Critical issues remain.

The single-source-of-truth timeout refactor is implemented correctly and consistently across the orchestrator and all three executors. Retry semantics, terminality of timeouts/transport errors, backward compatibility, and async correctness are all preserved. The `AttemptRecord` model and `reason` logging are correct and match `data-model.md`.

### Status of prior findings

#### M1 — Executors pass `properties.timeout`, not the effective timeout → **RESOLVED**
- **Evidence**:
  - Orchestrator computes the effective timeout exactly once and stores it on the context: `src/worker/retry.py:82-85` (`effective_timeout = float(properties.timeout) if properties.timeout is not None else get_settings().worker_http.timeout_seconds; ctx.effective_timeout = effective_timeout`).
  - The same value caps the attempt via `asyncio.wait_for(..., timeout=effective_timeout)` at `src/worker/retry.py:109`.
  - `RunContext` now carries the field: `src/lib/schemas/run.py:13` (`effective_timeout: float | None = None`).
  - Each executor applies `ctx.effective_timeout` (no longer `properties.timeout`) as the per-request timeout:
    - REST: `src/worker/executors/rest.py:27-28` → `httpx.Timeout(ctx.effective_timeout)`.
    - SSE: `src/worker/executors/sse.py:24-25` → `httpx.Timeout(ctx.effective_timeout)`.
    - chat_completion: `src/worker/executors/chat_completion.py:41-42` → `create_kwargs["timeout"] = ctx.effective_timeout`.
- The two timeout layers now derive from the **same** value regardless of `worker_http.timeout_seconds`. The `if ctx.effective_timeout is not None` guard is harmless: the orchestrator always sets it to a concrete float before invoking the executor, so the per-request timeout is always applied; the guard only protects an executor invoked outside the orchestrator (e.g., a direct unit test), which is correct defensive behavior.
- **Bonus correctness fix** (beyond round-1 scope, verified here): executors now capture HTTP status codes on errors — `rest.py:33-39`, `sse.py:39-45`, `chat_completion.py:53-56` raise `ScheduleRunError(..., status_code=...)` from `httpx.HTTPStatusError` / `APIStatusError`. This is what actually makes retryable-status matching work; transport errors (`rest.py:40-41`, `sse.py:46-47`) and the generic `Exception` path (`chat_completion.py:57-58`) correctly leave `status_code=None`, keeping them terminal.

#### M2 — `chat_completion` streaming timeout not bounded by per-request timeout → **RESOLVED**
- **Evidence**: `src/worker/executors/chat_completion.py:41-43` now passes `ctx.effective_timeout` (the orchestrator's single value) to `client.chat.completions.create(...)`, aligning the SDK request timeout with the authoritative `asyncio.wait_for` cap (`retry.py:109`). The streaming consumption loop (`chat_completion.py:48-52`) remains bounded by `asyncio.wait_for`, which is the authoritative cap as documented. The round-1 dual-source-of-truth concern is gone. No event-loop blocking introduced.

#### m1 — Non-retry failure due to missing status code logged generically → **RESOLVED**
- **Evidence**: `src/worker/retry.py:176-191` now emits an explicit `reason` on every non-retry failure log: `no_retry_policy` (`retry is None`), `no_status_code` (`status_code is None`), or `status_not_in_retryable`. The event name is still differentiated (`attempt_failed_non_retryable` when a retry policy exists, else `attempt_failed`), and the decision is now explicit in all branches. Matches FR-012 observability intent.

#### m2 — Retry/decision logs omit `schedule_id`/`run_id` → **RESOLVED**
- **Evidence**: Every decision log now passes both keys explicitly: `attempt_start` (`retry.py:99-100`), `attempt_timed_out` (`retry.py:129-130`), `retry_scheduled` (`retry.py:155-156`), `last_retry_failed` (`retry.py:168-169`), `attempt_failed*` (`retry.py:185-186`), `attempt_succeeded` (`retry.py:208-209`). The logs no longer depend transitively on caller-bound contextvars; Constitution II (task ID + identifier on scheduling-decision logs) is now satisfied directly.

#### Nit — `_attempt_record` 7-positional-param helper → **RESOLVED**
- **Evidence**: Replaced by a Pydantic `AttemptRecord` model (`src/worker/retry.py:16-26`) matching `data-model.md` §3 (`attempt`, `outcome`, `status_code`, `error_text`, `is_retry`, `is_last_attempt`, `timeout_seconds`, `timestamp`). The remaining `_record(...)` helper (`retry.py:46-65`) uses keyword-only params and builds the model, then `.model_dump()`s it for JSONB persistence. Field types align with the data-model (`status_code: int | None`, `error_text: str | None`, `timeout_seconds: float` — compliant with the doc's `int | float`). One minor naming note carried forward below.

#### n2 — Retryable sort recomputed each loop iteration → **RESOLVED**
- **Evidence**: Hoisted out of the loop to `retryable_log = sorted(retryable) if retryable else None` at `src/worker/retry.py:90`; reused in the per-attempt `attempt_start` log.

### Remaining items (non-blocking)

- **m3 (Open, no change required)** — Confirmed still correct: `last_retry_failed` vs `attempt_failed_non_retryable` branching at `retry.py:165-191` matches FR-012. Informational only.
- **m4 (Open, accepted)** — `timeout_seconds` is `float` even for integer input (`AttemptRecord.timeout_seconds: float`, `retry.py:25`). Compliant with `data-model.md`'s `int | float`; cosmetic.
- **m5 (Open, optional)** — Backward-compat path still relies on `ScheduleProperties(**kwargs)` defaulting `timeout`/`retry` to `None` (`jobs.py:78`). Verified correct (`effective_timeout` falls back to the default; `max_attempts = 1`). An explicit deserialization test of a pre-feature `kwargs` snapshot would lock SC-004 but is not required.
- **n5 (Open, pre-existing, out of scope)** — `rest.py:48` still logs full `response.text`; `chat_completion.py:45,50` log full response/chunk strings. No credentials are logged (API key lives only in request headers, never logged). Data-volume/PII hardening remains a future consideration, not introduced or worsened by this branch.
- **New minor (n6, informational)** — Naming: the model is `AttemptRecord` but the builder is `_record`. Consider renaming `_record` → `_attempt_record` for symmetry with the model and prior helper name. Purely cosmetic.

### Regression check — all clear
- Timeouts and transport errors remain terminal (`status_code=None` → `should_retry` is `False`): `retry.py:110-134` (timeout) and `retry.py:138`; executor transport/generic paths leave `status_code=None`.
- Retry semantics unchanged: `max_attempts = 1 + len(backoff)` (`retry.py:91`), `should_retry = retries_remain and status_code is not None and status_code in retryable` (`retry.py:138`), `backoff[attempt - 1]` wait via `await asyncio.sleep(...)` (`retry.py:152,162`) — no event-loop blocking.
- Backward compatibility intact (m5 above).
- No `assert`, no `print()`, no bare `except` in the reviewed code; the broad `except Exception` in `chat_completion.py:57` is justified for the OpenAI SDK surface and re-raises a typed `ScheduleRunError`.
- No new secret leakage; `dial_api_key` is read via `get_secret_value()` only into request headers, never logged.
- Test suite green: 398 passed / 92 skipped; 52 targeted retry+executor tests pass.

### Data-model alignment (AttemptRecord & reason)
- `AttemptRecord` (`retry.py:16-26`) matches `data-model.md` §3 field-for-field, including `int | None` / `str | None` nullability and ISO-8601 UTC `timestamp` (`datetime.now(UTC).isoformat()`, `retry.py:64`).
- `is_last_attempt` correctly reflects run-ending semantics: `True` on timeout/success, and `not should_retry` on `ScheduleRunError` (`retry.py:146`), consistent with the data-model examples.
- The `reason` values (`no_retry_policy` / `no_status_code` / `status_not_in_retryable`) are an additive log field; they are not part of the persisted `AttemptRecord` shape and so do not affect data-model conformance.

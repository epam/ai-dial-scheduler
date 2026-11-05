# Code Review 1 — APScheduler Executor Worker

**Spec dir**: `specs/008-apscheduler-executor-worker/`
**Date**: 2026-06-11
**Mode**: Fresh review
**Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `contracts/dispatch-handler.md`, `contracts/executor-interface.md`, `contracts/kwargs-schema.md`

## Summary

Requires changes. The dispatch path, validation, error correlation, and heartbeat are well built and match the contracts. However there are correctness/reliability defects in the worker lifecycle (signal handling and the never-cancelled poller task), a contextvars leak that violates the heartbeat "logs nothing else" requirement (US3 / SC), and a heartbeat-vs-shared-store ownership gap (the heartbeat is registered but `replace_existing` targets the default store semantics). Several executor edge cases (SSE non-2xx surfacing, chat streaming record shape) diverge subtly from the contracts. Findings below ordered critical → major → minor.

---

## Finding 1: Signal handler sets asyncio.Event from a non-async-safe context
**File**: `src/worker/app.py:74-75`
**Severity**: major
**Description**: `signal.signal(sig, lambda *_: stop_event.set())` invokes `asyncio.Event.set()` directly from the OS signal handler. `asyncio.Event` is not thread/signal-safe; `set()` from a C-level signal handler can run between event-loop steps and, more importantly, `set()` alone does not wake a loop that is blocked in `await stop_event.wait()` if the wakeup falls outside the loop's selector cycle. On the asyncio loop this often appears to work but is not guaranteed and is explicitly discouraged. The portable, loop-safe path is `loop.add_signal_handler` (Unix) with a `signal.signal` fallback that uses `loop.call_soon_threadsafe(stop_event.set)`.
**Suggestion**:
```python
loop = asyncio.get_running_loop()

def _request_stop() -> None:
    loop.call_soon_threadsafe(stop_event.set)

for sig in (signal.SIGTERM, signal.SIGINT):
    try:
        loop.add_signal_handler(sig, _request_stop)
    except NotImplementedError:  # Windows
        signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop_event.set))
```

## Finding 2: `_jobstore_poller` task is never cancelled or awaited on shutdown
**File**: `src/worker/app.py:88-95`
**Severity**: major
**Description**: `asyncio.create_task(_jobstore_poller(...))` is fire-and-forget. The returned task is not stored, not cancelled, and not awaited in the `finally` block. On shutdown the task is still sleeping in `asyncio.sleep(interval)`; `asyncio.run` will emit "Task was destroyed but it is pending" warnings and may call `scheduler.wakeup()` after `scheduler.shutdown()` has run, raising on a stopped scheduler. The stop_event check only happens after the sleep completes, so there is up to `interval_seconds` of lag before the loop notices the stop.
**Suggestion**: Keep a reference and cancel it explicitly:
```python
poller = asyncio.create_task(_jobstore_poller(scheduler, stop_event))
try:
    await stop_event.wait()
finally:
    poller.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await poller
    scheduler.shutdown(wait=True)
    await http_client.aclose()
    clear_worker_context()
```
Also consider replacing `await asyncio.sleep(interval)` with `await asyncio.wait_for(stop_event.wait(), timeout=interval)` so shutdown is immediate.

## Finding 3: contextvars not cleared after a run — leaks `run_id`/`schedule_id` into the heartbeat and subsequent jobs
**File**: `src/worker/jobs.py:17-30`, heartbeat at `33-34`
**Severity**: major
**Description**: `execute_scheduled_job` calls `clear_contextvars()` at the start but never clears them at the end (success or failure). Because the worker uses a single event loop and `structlog.contextvars` are `contextvars` bound on the running task/context, a heartbeat firing after a real run — or any log emitted by APScheduler internals between runs — can inherit the previous run's `run_id`/`schedule_id`. This directly violates US3 acceptance scenario 1 and the data-model "Heartbeat … and nothing else" requirement: the heartbeat must log only `event`, `job_name`, `executed_at`. The unit test `test_jobs.py::test_logs_nothing_else` passes only because it runs in isolation with a fresh contextvars state; it would not catch a real leak across jobs.
**Suggestion**: Wrap the run body so contextvars are always cleared, and/or clear at the top of `heartbeat`:
```python
async def execute_scheduled_job(schedule_id: str, **kwargs: Any) -> None:
    run_id = str(uuid4())
    clear_contextvars()
    bind_contextvars(run_id=run_id, schedule_id=schedule_id)
    logger.info("run_start", schedule_id=schedule_id, run_id=run_id)
    try:
        ...
    finally:
        clear_contextvars()
```
Note this interacts with Finding 6 (the error listener reads ids off the exception, not contextvars, so clearing in `finally` is safe).

## Finding 4: SSE non-2xx-before-stream-open is not surfaced as a failure in the contract-specified way
**File**: `src/worker/executors/sse.py:23-36`; contract `executor-interface.md:46-48`
**Severity**: major
**Description**: The contract requires "Non-2xx before stream open ⇒ `ScheduleRunError`". `httpx_sse.aconnect_sse` opens the response via `client.stream(...)` and does **not** call `raise_for_status()`. A non-2xx response is only turned into an error indirectly: when `aiter_sse()` runs `_check_content_type()` and the error response is not `text/event-stream`, it raises `SSEError`. In httpx_sse 0.4.3 `SSEError` happens to subclass `httpx.TransportError`, so it is caught here — but this is incidental, not guaranteed across versions, and it does not cover a non-2xx response that *does* carry `Content-Type: text/event-stream` (the worker would then stream the error body as if it were success events). The status code is never inspected.
**Suggestion**: Explicitly check the response status before iterating:
```python
async with aconnect_sse(client, str(properties.method), properties.url, ...) as event_source:
    if event_source.response.status_code >= 400:
        event_source.response.raise_for_status()
    async for event in event_source.aiter_sse():
        ...
```
and broaden the except to also catch `httpx_sse.SSEError` (or `httpx.HTTPError`) so the dependency-version coupling is removed.

## Finding 5: Chat streaming uses `completions.stream()` whose events are not "chunks" per the contract
**File**: `src/worker/executors/chat_completion.py:41-46`; contract `executor-interface.md:33`, `kwargs-schema.md`
**Severity**: major
**Description**: The contract/spec define streaming as `payload.stream == true ⇒ async for chunk → one record per chunk`, i.e. raw `ChatCompletionChunk` objects from `create(stream=True)`. The implementation instead uses the SDK helper `client.chat.completions.stream(**payload)`, which yields high-level `ChatCompletionStreamEvent` objects (e.g. `chunk`, `content.delta`, `content.done`), producing a different count and shape of records than the spec's per-chunk model and breaking SC-004 ("count of logged chunk records equals the number of chunks received"). It also silently drops the `stream` flag rather than passing it to the API. The validator injects `stream=False` into the payload for non-stream, but the executor pops `stream` and never forwards it.
**Suggestion**: Use the streaming variant of `create` to stay faithful to the contract:
```python
if stream:
    response_stream = await client.chat.completions.create(**payload, stream=True, extra_headers=properties.extra_headers)
    async for chunk in response_stream:
        logger.info("stream_chunk", run_id=run_id, chunk=str(chunk))
else:
    response = await client.chat.completions.create(**payload, extra_headers=properties.extra_headers)
    logger.info("run_result", run_id=run_id, result=str(response))
```
If the `.stream()` helper is intentionally preferred, update the contract and the per-event semantics accordingly. Note the unit test mocks `.stream()` so it cannot detect this divergence from the spec.

## Finding 6: Executors read `schedule_id` from contextvars with an empty-string fallback — can violate SC-002
**File**: `src/worker/executors/rest.py:28-29`, `sse.py:34-36`, `chat_completion.py:50-52`
**Severity**: major
**Description**: Each executor recovers `schedule_id` via `get_contextvars().get("schedule_id", "")`. SC-002 / FR-014 require *every* `ScheduleRunError` to carry both ids. If contextvars were cleared or not bound (e.g. after Finding 3's fix, or if an executor is ever called outside the dispatch handler), the error is raised with `schedule_id=""`, silently satisfying the type but failing the requirement. The schedule_id is known to the caller and should be passed explicitly rather than recovered from ambient state — this also removes the executors' hidden dependency on the dispatcher's contextvars binding (cleaner per Constitution IV/V).
**Suggestion**: Make the executor interface take identity explicitly, e.g. `execute(self, run_id: str, schedule_id: str, properties: ScheduleProperties)`, or pass a `RunContext` (which already exists in `src/lib/schemas/run.py` but is currently unused). The dispatcher already has `schedule_id` in scope. This also makes `RunContext`/`RunResult` earn their place — see Finding 10.

## Finding 7: `init_telemetry` is imported and called via `importlib`/`getattr` with a blanket `except Exception: pass`
**File**: `src/worker/app.py:77-84`
**Severity**: minor
**Description**: The plan (Constitution II) wires `init_telemetry` for tracing. The implementation reaches it through `importlib.import_module("aidial_sdk")` + `hasattr` + `# type: ignore`, swallowing all exceptions silently. If telemetry init fails, nothing is logged, and the dynamic import defeats static checking. Since `aidial-sdk` is a declared dependency, import it directly.
**Suggestion**:
```python
from aidial_sdk.telemetry.init import init_telemetry  # or the documented path
try:
    init_telemetry(app=None, config=...)
except Exception as exc:
    logger.warning("telemetry_init_failed", error=str(exc))
```
At minimum log the swallowed exception rather than `pass`.

## Finding 8: `on_job_error` event parameter is untyped; non-`ScheduleRunError` branch loses correlation
**File**: `src/worker/app.py:34-39`
**Severity**: minor
**Description**: `def on_job_error(event) -> None` has no type hint on `event` (project convention requires hints on all signatures; `mypy` likely infers `Any`). Use `apscheduler.events.JobExecutionEvent`. Separately, the `else` branch logs `job_error` with no `schedule_id`/`run_id`; for a non-`ScheduleRunError` the `event.job_id` is available and would aid correlation — consider including it.
**Suggestion**:
```python
from apscheduler.events import JobExecutionEvent

def on_job_error(event: JobExecutionEvent) -> None:
    exc = event.exception
    if isinstance(exc, ScheduleRunError):
        logger.error("run_failed", schedule_id=exc.schedule_id, run_id=exc.run_id, error=str(exc))
    else:
        logger.error("job_error", job_id=event.job_id, error=str(exc))
```

## Finding 9: REST/SSE pass per-request `timeout=` though the shared client is already constructed with it
**File**: `src/worker/executors/rest.py:24`, `sse.py:30`; client created at `app.py:59`
**Severity**: minor
**Description**: The `httpx.AsyncClient` in `WorkerContext` is already created with `timeout=settings.http_timeout_seconds` (`app.py:59`). Re-passing `timeout=settings.http_timeout_seconds` on every `client.request(...)` / `aconnect_sse(...)` is redundant. It is harmless (same value) but duplicates the source of truth and invites drift if one is changed. Pick one place — preferably the client construction — to own the timeout.
**Suggestion**: Drop the per-call `timeout=` in both executors, or document that per-call is intentional and remove it from the client.

## Finding 10: `RunContext` / `RunResult` schemas are defined but never used
**File**: `src/lib/schemas/run.py`
**Severity**: minor
**Description**: `data-model.md` introduces `RunContext` (to bind into contextvars) and `RunResult` (standardized success object shaped for logging). Neither is imported anywhere in `src/worker/`. The dispatcher binds raw kwargs into contextvars and executors log ad-hoc keyword dicts. This is dead code per YAGNI, or — better — these models should actually be used to standardize the logging shape and the explicit identity passing in Finding 6.
**Suggestion**: Either remove `run.py` (YAGNI) or adopt it: use `RunContext` for the contextvars binding / explicit identity, and emit `RunResult.model_dump()` for the single-result records so the "standardized success object" claim in the spec holds.

## Finding 11: SSE manually injects `Accept`, but `aconnect_sse` overwrites it — extra_headers cannot win on `Accept` collision
**File**: `src/worker/executors/sse.py:17-21`
**Severity**: minor
**Description**: The executor builds headers as `{Api-Key, Accept, **extra_headers}` (so extra_headers would win on `Accept` per FR-013 / kwargs-schema collision rule). But `httpx_sse.aconnect_sse` pops `headers` and unconditionally sets `headers["Accept"] = "text/event-stream"` and `headers["Cache-Control"] = "no-store"`, so any caller `Accept` (including an extra_headers override) is discarded. The manually-added `Accept` line is therefore dead, and the documented "extra_headers win on Accept collision" edge case is silently not honored for SSE.
**Suggestion**: Remove the redundant `Accept` injection (rely on `aconnect_sse`), and note in the contract that `Accept`/`Cache-Control` are owned by the SSE transport and cannot be overridden by `extra_headers`. If overriding must be supported, set the header after connect is not possible — document the limitation.

## Finding 12: `str(properties.url)` / `str(properties.method)` rely on enum/str stringification
**File**: `src/worker/executors/rest.py:20-21`, `sse.py:26`
**Severity**: minor
**Description**: `properties.method` is `HttpMethod | None` (a `StrEnum`); for REST/SSE the validator guarantees non-null, so `str(properties.method)` yields `"POST"` (StrEnum str is the value). This is correct but implicit. `properties.url` is already a `str` (the model stores `url: str`), so `str(properties.url)` is a no-op. Minor: drop the redundant `str()` on `url`, and consider passing `properties.method.value` for explicitness/readability. No functional bug.
**Suggestion**: `url=properties.url` (it is already `str`); `method=properties.method.value`.

---

## Intent Alignment

- FR-004 / SC-002 (every failure carries both ids): partially at risk via Finding 6 (empty-string fallback) and Finding 3 (premature/leaked contextvars).
- FR-010 / SC-004 (one record per chunk for chat streaming): diverges via Finding 5 (`.stream()` events vs raw chunks).
- FR-013 (SSE behaves like REST + stream preference, extra_headers win on collision): Finding 11 — `Accept` collision rule not honored.
- US3 / heartbeat "nothing else": at risk via Finding 3.
- FR-002 (worker registers exactly one job): correctly implemented — heartbeat in the `local` MemoryJobStore, `replace_existing=True`, never in the shared `default` store. Verified by `test_worker_app.py`.
- FR-005/FR-007 (no DB read, config from kwargs): correctly honored — dispatch validates kwargs directly, no DB access in the path.
- FR-018 (configured timeout): honored (see Finding 9 re: duplication).
- `dial_api_key` never logged: honored — key is only placed in request headers / SDK `api_key`, never in a log call; tests assert this for each executor.

## Checklist Pass (areas with no issues found)

- Custom exception `ScheduleRunError(message, schedule_id, run_id)` matches the contract and subclasses `SchedulerError`.
- Dispatcher `TargetType → Executor` static map is clean and total over the enum (KISS/YAGNI).
- No raw SQL; no DB access in the worker dispatch path.
- No `print()`; structured `logger` used throughout.
- No reading of `.env`/secrets in feature code.
- Validation reuses the existing `ScheduleProperties` model verbatim, as the contract requires.
- Type hints present on the executor protocol, jobs, context, and lib modules (gap: `on_job_error` event param, Finding 8).
- Test layout mirrors `src/` and uses `pytest-mock` + `structlog.testing.capture_logs`.

---

## Counts

- **Critical**: 0
- **Major**: 6 (Findings 1, 2, 3, 4, 5, 6)
- **Minor**: 6 (Findings 7, 8, 9, 10, 11, 12)

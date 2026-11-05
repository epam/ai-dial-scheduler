# Phase 1 Data Model: Split Run Execution Timeouts

No database entities change. This document covers the Pydantic models and settings that carry the two bounds from the API to the executor.

## Persistence

| Store | Column | Change |
|-------|--------|--------|
| `schedules` | `kwargs` (JSONB) | None. `idle_timeout` is a new key inside the serialized `ScheduleProperties`. |
| `schedule_runs` | `schedule_snapshot` (JSONB) | None. Carries whatever `kwargs` held at firing time. |
| `schedule_runs` | `result` (JSONB) | None. Per-attempt detail stays logs-only (020 R5). |

**No Alembic migration is produced by this feature** (SC-008).

Existing rows deserialize with `idle_timeout` absent → `None` → process default applies.

## `ScheduleProperties` (`src/lib/schemas/schedule.py:85`)

### New field

| Field | Type | Default | Constraint |
|-------|------|---------|------------|
| `idle_timeout` | `float \| None` | `None` | `> 0` when set |

`float`, matching the existing `timeout: float | None = None` at `:94`. (Note: feature 020's contract document describes `timeout` as an integer; the code has always been `float`, and that document is stale.)

### Unchanged field, restated

| Field | Type | Default | Constraint |
|-------|------|---------|------------|
| `timeout` | `float \| None` | `None` | `> 0` when set |

Meaning is unchanged: the absolute ceiling on one attempt. Existing validator at `:115-120` stays as is.

### Validation

- **Field validator on `idle_timeout`** — mirrors `validate_timeout`: reject non-positive values with a message naming the field and its unit.
- **Cross-field validator** — reject `idle_timeout > timeout` **only when both are explicitly set**. When either is `None`, the comparison would be against a process default, which would make a stored schedule's validity depend on deployment configuration.

The existing `validate_target_rules` model validator is unchanged. Its injection of `payload["stream"] = False` when absent is what guarantees the new `is_streaming` property is always well-defined.

### New property: `is_streaming`

`True` for `sse`; for `chat_completion`/`responses` it follows `payload.stream`; `False` for `rest`. Single source of truth for a question asked in two places — the orchestrator picks the ceiling default by it, and the two SDK executors pick the call shape by it.

### Descriptions

Both fields gain `Field(description=...)`. These strings are what MCP clients receive as tool-input guidance, so they must state the distinction in one line each — ceiling versus gap-between-events — and name the unit.

## `RunContext` (`src/lib/schemas/run.py`)

Current fields: `run_id`, `schedule_id`, `http_client`, `default_timeout`, `effective_timeout`.

### New

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `timeouts` | `RunTimeouts \| None` | `None` | The owning process's bounds; `None` means "fall back to this process's own `WORKER_HTTP_*` settings" |
| `effective_idle_timeout` | `float \| None` | `None` | The resolved idle bound for this attempt |

### New model: `RunTimeouts`

| Field | Type | Meaning |
|-------|------|---------|
| `max_duration` | `float` | Ceiling default for a non-streaming target |
| `streaming_max_duration` | `float` | Ceiling default for a streaming target |
| `idle_timeout` | `float` | Idle-bound default |

Grouped rather than passed as three floats so that `BackgroundRunLauncher.__init__` stays within the Principle V parameter limit, and so the three cannot drift apart — an earlier revision threaded the ceiling per-process while leaving the idle bound reading from the worker's settings, which made `API_HTTP_IDLE_TIMEOUT_SECONDS` inert.

`effective_timeout` keeps its current meaning — the resolved ceiling.

### Removed: `default_timeout`

`default_timeout` backed the owning process's `DialClient` calls (`src/worker/jobs.py`, `src/worker/conversation.py`) and was never an attempt bound. It is removed together with `timeout_seconds` (see below and research.md R11): `DialClient` now inherits the timeouts configured on `http_client`, so there is nothing left for the context to carry. `BackgroundRunLauncher.__init__` loses the matching parameter and is now `(http_client, timeouts)`.

Executors read `effective_timeout` and `effective_idle_timeout` from the context only, never from `properties` (FR-013).

## HTTP settings (`src/lib/settings.py`)

Three new fields on **each** of `ApiHttpSettings` (`env_prefix="API_HTTP_"`) and `WorkerHttpSettings` (`env_prefix="WORKER_HTTP_"`):

| Field | Default | Environment variable (API / worker) |
|-------|---------|-------------------------------------|
| `max_duration_seconds` | `600.0` | `API_HTTP_MAX_DURATION_SECONDS` / `WORKER_HTTP_MAX_DURATION_SECONDS` |
| `streaming_max_duration_seconds` | `3600.0` | `API_HTTP_STREAMING_MAX_DURATION_SECONDS` / `WORKER_HTTP_STREAMING_MAX_DURATION_SECONDS` |
| `idle_timeout_seconds` | `60.0` | `API_HTTP_IDLE_TIMEOUT_SECONDS` / `WORKER_HTTP_IDLE_TIMEOUT_SECONDS` |

Existing fields on both classes are untouched: `connect_timeout_seconds` (`5.0`), `read_timeout_seconds`, `write_timeout_seconds`, `pool_timeout_seconds`, `max_connections`, `max_keepalive_connections`.

### Removed: `timeout_seconds`

`timeout_seconds` (`API_HTTP_TIMEOUT_SECONDS` / `WORKER_HTTP_TIMEOUT_SECONDS`) is removed from both classes. After the split it no longer bounded runs, httpx ignored it as the positional default of each fully-specified `httpx.Timeout`, and its one live use — `DialClient` — replaced the client's per-phase timeouts with a single flat value. Both classes use `extra="ignore"`, so a deployment that still sets the variable starts normally. See research.md R11.

## Resolution order

Performed once per attempt in `src/worker/retry.py` and written onto the context before the executor runs.

```
defaults = ctx.timeouts  ??  RunTimeouts(from this process's worker_http settings)

ceiling_default = defaults.streaming_max_duration  if properties.is_streaming
                  else defaults.max_duration

effective_timeout      = properties.timeout       ?? ceiling_default
effective_idle_timeout = properties.idle_timeout  ?? defaults.idle_timeout
```

All three defaults come from the same `RunTimeouts`, so a process either supplies all of its bounds or none of them.

## Enforcement sites

| Bound | Where | Mechanism |
|-------|-------|-----------|
| Ceiling | `src/worker/retry.py:95` | `asyncio.wait_for(executor.execute(...), timeout=effective_timeout)` — unchanged call, new default |
| Idle (streaming) | `sse.py`, `chat_completion.py`, `responses.py` streaming branches | `asyncio.timeout(effective_idle_timeout)` around each `anext()` on the stream iterator |
| HTTP read (non-streaming) | `rest.py`, and the non-streaming branches of `chat_completion.py` / `responses.py` | read phase set to the ceiling via the shared helper |
| HTTP connect/write/pool | all four executors | preserved from the client by the shared helper, no longer clobbered |

## New helpers (`src/worker/executors/base.py`)

`base.py` currently holds only the `Executor` Protocol (`:8-11`). Two additions:

- **`build_timeout(client, read_seconds) -> httpx.Timeout`** — returns a timeout whose `read` is the given value and whose `connect`, `write` and `pool` come from the client's configured timeout. Replaces the four bare-float / single-positional constructions.
- **An idle-bounded iteration wrapper** — yields events from an async iterator, raising a `ScheduleRunError` that names stream inactivity if any single `anext()` exceeds the idle bound. Shared by the three streaming paths so the error message and the bound's semantics are identical across them.

## Failure modes

| Trigger | Raised as | Logged as | Retryable? |
|---------|-----------|-----------|------------|
| Ceiling expires | `asyncio.TimeoutError` caught in `retry.py` → `ScheduleRunError("execution timed out after {n}s")` | `attempt_timed_out` | No |
| Idle bound expires | `StreamIdleTimeoutError("stream produced no event for {n}s")` from the executor | `attempt_idle_timed_out` | No |

`StreamIdleTimeoutError` subclasses `ScheduleRunError`, so existing handling is unaffected, but the orchestrator branches on it to emit a distinct event. Without the subclass an idle expiry would log as `attempt_failed` with `reason=no_status_code` — identical to any transport error — and FR-011 would be met only by free text.

Neither is retryable: retry matches on a non-`None` `status_code` and a timeout carries none. Unchanged behaviour, stated because it is a common misreading.

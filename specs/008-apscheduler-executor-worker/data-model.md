# Phase 1 Data Model: APScheduler Executor Worker

This worker is **read-only** with respect to storage and persists nothing. "Entities" here are
the in-memory/typed structures it operates on plus the read-only rows it consults.

## Read-only persisted entities (owned elsewhere)

### `apscheduler_jobs` — never read by feature code
Owned by APScheduler's `SQLAlchemyJobStore`. The worker never queries it and never deserializes
`job_state`. Source of *when/what* fires only. The fired job's kwargs carry the task config.

> **Note**: The worker dispatch path performs **no** database reads. The `schedules` mirror is
> owned and read by the creator service; this worker takes its authoritative config from the
> kwargs APScheduler passes the handler (decision D1/D2 in [research.md](./research.md)).

## Validated config entity

### `ScheduleProperties` (existing — `src/lib/schemas/schedule.py`)
Reused verbatim as the validation target for the kwargs the dispatch handler receives. Rules
already enforced:

| Field | Type | Rule |
|-------|------|------|
| `target_type` | `TargetType` enum | `chat_completion` \| `rest` \| `sse`; selects executor. |
| `method` | `HttpMethod \| None` | Must be **unset** for `chat_completion`; **required** for `rest`/`sse`. |
| `url` | str | Valid HTTP URL (`AnyHttpUrl`). |
| `payload` | dict | For `chat_completion`, `stream` is injected as `False` when absent. |
| `extra_headers` | dict[str,str] | Wins on header collision with injected headers. |
| `api_version` | str \| None | Allowed **only** for `chat_completion`. |

Validation failure ⇒ run failure (`ScheduleRunError`).

## New typed entities (this feature)

### `ScheduleRunError` — `src/lib/exceptions/__init__.py`
Subclass of `SchedulerError`. Carries the run's identity through the failure path.

| Attribute | Type | Notes |
|-----------|------|-------|
| `schedule_id` | str | The failing schedule. |
| `run_id` | str (UUID4) | The failing run. |
| `message`/`__cause__` | str / Exception | Underlying reason (validation, transport, non-2xx). |

Raised on: validation failure, transport error, non-success response.
Surfaced by the `EVENT_JOB_ERROR` listener (logging only).

### `RunContext` — `src/lib/schemas/run.py` (new, Pydantic v2)
Lightweight identity model passed explicitly to each executor. **Not persisted** — used to
correlate log records and populate `ScheduleRunError`.

- `RunContext`: `run_id: str`, `schedule_id: str` — bound into `structlog.contextvars` for the
  duration of a run so every executor log line is correlated; also passed as `ctx` argument.

### `WorkerContext` — `src/worker/context.py` (new)
Frozen dataclass holding process-shared resources for the worker's lifetime.

| Field | Type | Notes |
|-------|------|-------|
| `http_client` | `httpx.AsyncClient` | Shared by `rest` and `sse` executors; created with `http_timeout_seconds`. |

Lifecycle: created in `run_worker`, retrieved via `get_worker_context()`, cleared/closed on
shutdown. Not a module-level mutable global (accessor-guarded; see research D6).

## Executor interface (typed contract)

`Executor` (ABC/Protocol in `src/worker/executors/base.py`):

```text
async def execute(ctx: RunContext, properties: ScheduleProperties) -> None
```

Implementations: `ChatCompletionExecutor`, `RestExecutor`, `SseExecutor`. Each logs results as
they arrive and raises `ScheduleRunError` on failure. Dispatch (`src/worker/dispatcher.py`)
resolves `TargetType → Executor` via a static mapping.

## Logging records (no persistence — observability only)

| Event | Key fields |
|-------|-----------|
| Heartbeat | `event`, `job_name`, `executed_at` (ISO-8601 UTC). |
| Run start | `run_id`, `schedule_id`, `target_type`. |
| Single result (chat non-stream / rest) | `run_id`, `schedule_id`, `result`/`status`. |
| Stream chunk (chat stream) | `run_id`, `schedule_id`, `chunk`. |
| SSE event | `run_id`, `schedule_id`, `{event_name: raw_string}`. |
| Failure (via listener) | `run_id`, `schedule_id`, `error`. |

`dial_api_key` MUST NOT appear in any record.

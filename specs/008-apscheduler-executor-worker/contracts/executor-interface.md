# Contract: Executor Interface

All three executors share one interface and one failure contract. Dispatch resolves the
implementation from `target_type`.

## Interface

```text
class Executor(Protocol):
    async def execute(self, ctx: RunContext, properties: ScheduleProperties) -> None: ...
```

- `ctx: RunContext` carries `run_id` and `schedule_id` for the current run.
- Logs results as they arrive (Constitution II): one record for non-streaming; one record per
  chunk/event for streaming, emitted as each arrives (not buffered) — SC-004.
- Raises `ScheduleRunError(message, schedule_id=ctx.schedule_id, run_id=ctx.run_id)` on any failure.
- Never persists results.

## `ChatCompletionExecutor`

| Input | Source |
|-------|--------|
| `azure_endpoint` | `scheme://netloc` of `properties.url` (path discarded). |
| `api_key` | `settings.dial_api_key` (never logged). |
| `api_version` | `properties.api_version`. |
| `azure_deployment` | path segment after `deployments/` in `properties.url`. |
| call | `client.chat.completions.create(**properties.payload, extra_headers=properties.extra_headers)`. |

- `properties.method` ignored.
- Single response ⇒ one logged result record.
- `payload.stream == true` ⇒ `async for` chunk → one record per chunk.

## `RestExecutor`

- `httpx` request: `method=properties.method` (non-null), `url=properties.url`,
  `json=properties.payload`, `headers={"Api-Key": dial_api_key, **properties.extra_headers}`.
- Timeout = `settings.http_timeout_seconds`.
- Non-2xx ⇒ `ScheduleRunError`. 2xx ⇒ one logged response record.
- Uses the shared `httpx.AsyncClient` from `WorkerContext`.

## `SseExecutor`

- Same request shape as `RestExecutor`; `aconnect_sse` sets `Accept: text/event-stream` unconditionally — callers must not inject it.
- Streams via `httpx_sse.aconnect_sse`; iterates events; logs each as `{event_name: raw_string}`.
- Non-2xx before stream open ⇒ `ScheduleRunError`. Stream end = connection close / iterator
  exhaustion. Zero events ⇒ success with no per-event records.
- Uses the shared `httpx.AsyncClient` from `WorkerContext`.

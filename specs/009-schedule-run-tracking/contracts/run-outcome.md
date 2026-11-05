# Contract: RunOutcome & Executor Return Type

Internal typed contracts (no REST surface). All cross-boundary data is typed — no ad-hoc dicts
escape the executor/job boundary (Constitution IV).

## `RunOutcome` (Pydantic v2) — `src/lib/schemas/run.py`

The standardized object a successful job returns. APScheduler carries it as
`JobExecutionEvent.retval` to the `EVENT_JOB_EXECUTED` listener.

```python
class RunOutcome(BaseModel):
    run_id: str
    schedule_id: str
    result: dict[str, Any]
```

- `run_id` MUST equal the `run_id` of the `in_progress` row created at job start.
- `result` MUST be a JSON object (dict). It is stored verbatim into `schedule_runs.result`.

## Executor return type — `src/worker/executors/base.py`

```python
class Executor(Protocol):
    async def execute(self, ctx: RunContext, properties: ScheduleProperties) -> dict[str, Any]: ...
```

The executor returns the **accumulated result object**. It MUST continue to emit the per-event /
per-chunk / per-result structured logs from feature 008 (observability preserved) in addition to
returning the accumulated value.

### Per-executor result shapes

| Executor | Success result shape | Notes |
|----------|----------------------|-------|
| `RestExecutor` | `{"status_code": int, "body": <parsed-json \| str>}` | `body` is parsed JSON when the response is JSON, else the response text. |
| `SseExecutor` | `{"events": [{"event": str, "data": str}, ...]}` | Accumulates every SSE event in arrival order. |
| `ChatCompletionExecutor` | non-stream: `{"result": <completion-as-json>}`; stream: `{"chunks": [<chunk-as-json>, ...]}` | One key depending on `payload["stream"]`. |

### Failure (all executors)

On any transport/HTTP/SDK failure the executor raises
`ScheduleRunError(str(exc), schedule_id=ctx.schedule_id, run_id=ctx.run_id)` (unchanged from
feature 008). It MUST NOT return a partial result on failure.

## Security

`result` and any logged payload MUST NOT contain `Api-Key` / `dial_api_key` or other
credentials (FR-012). Result shapes above intentionally carry only response data.

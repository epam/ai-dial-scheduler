# Contract: ResponsesExecutor

**Module**: `src/worker/executors/responses.py`
**Registered in**: `src/worker/dispatcher.py` → `_EXECUTOR_MAP[TargetType.RESPONSES] = ResponsesExecutor()`

## Interface

Implements the existing `Executor` protocol (`src/worker/executors/base.py`):

```python
async def execute(
    self, ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties
) -> dict[str, Any]: ...
```

## Behavior (MUST)

1. **Auth**: Derive the api-key from `credentials.header_value`, stripping a leading `Bearer ` (`strip_bearer_prefix`), identical to `ChatCompletionExecutor`.
2. **Base URL**: Unlike chat completion (which uses a per-deployment URL path `.../deployments/{id}/chat/completions`), the Responses API is invoked against a **base URL** (e.g. `.../openai/v1`) with the model supplied in the payload. `properties.url` is passed to the client verbatim as `base_url`; the SDK appends the `responses` path. The deployment/model is **not** parsed from the URL.
3. **Client**: Construct `AsyncAzureOpenAI(base_url=properties.url, api_key=..., api_version=properties.api_version, http_client=HttpClientProvider().client)`.
4. **Request kwargs**: `{**properties.payload, "extra_headers": properties.extra_headers}`, then pop `stream` from the kwargs. `payload.model` (required by validation) flows through to `responses.stream(...)` and identifies the deployment. Apply `ctx.effective_timeout` as `timeout` when set.
5. **Invocation** (Responses API):
   - Assemble a single final `Response` via `client.responses.stream(**kwargs)` and `await stream.get_final_response()`; while iterating, log stream events by `type` only.
   - The stored result is the same assembled shape for streaming and non-streaming runs (the `stream` flag in `payload` is honored/normalized by `ScheduleProperties`; the executor produces one assembled `Response` regardless).
6. **Result**: Return `{"result": response.model_dump()}`.

## Error mapping (MUST)

| Exception | Mapped to |
|-----------|-----------|
| `openai.APIStatusError` | `ScheduleRunError(str(exc), schedule_id, run_id, status_code=exc.status_code)` — enables retry eligibility by status code |
| Any other `Exception` | `ScheduleRunError(str(exc), schedule_id, run_id)` — no status code (terminal, like transport errors) |

This mirrors `ChatCompletionExecutor` exactly so `execute_with_policy` retry/timeout semantics are identical.

## Observability (MUST)

- Log stream events with `event_type = getattr(event, "type", None)` at DEBUG, keyed by `run_id`. **Never** log streamed content, prompt, response text, or credentials (FR-010).

## Result shape contract

```json
{ "result": { /* openai Response.model_dump() */ } }
```

Consumed by run inspection (`RunResponse.result`). The scheduler does not interpret the Response body.

## Non-goals

- No conversation creation (out of scope; `create_conversation` is rejected for `responses`).
- No changes to `execute_with_policy`, `jobs.py`, run tracking, or the dispatcher's resolution mechanism beyond the one map entry.

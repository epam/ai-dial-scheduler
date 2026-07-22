# Contract: ChatCompletionExecutor — base URL + payload model

**Feature**: `029-base-url-model-field` | **Module**: `src/worker/executors/chat_completion.py`

## Signature (unchanged)

```python
async def execute(
    self, ctx: RunContext, credentials: OboCredentials, properties: ScheduleProperties
) -> dict[str, Any]: ...
```

## Behavior (after this feature)

1. Derive `api_key` from `credentials.header_value` via `strip_bearer_prefix` *(unchanged)*.
2. Construct the client with the **base URL** (no deployment parsing):

   ```python
   client = AsyncAzureOpenAI(
       base_url=properties.url,
       api_key=api_key,
       api_version=properties.api_version,
       http_client=HttpClientProvider().client,
   )
   ```

   - `parse_deployment`, `azure_endpoint`, and `azure_deployment` are **removed**.
   - The deployment is selected by `payload["model"]`, which flows into the request via
     `create_kwargs` (the SDK sends it in the request body). Validation guarantees it is present.
3. Build `create_kwargs = {**properties.payload, "extra_headers": properties.extra_headers}`; pop
   `stream` (default `False`); apply `ctx.effective_timeout` when set. *(unchanged)*
4. Streaming: `client.chat.completions.stream(...)` + `get_final_completion()`; non-streaming:
   `client.chat.completions.create(...)`. Log stream event `type` only — never content. *(unchanged)*
5. Return `{"result": result.model_dump()}` — **identical stored result shape** to before. *(unchanged)*

## Error mapping (unchanged)

- `APIStatusError` → `ScheduleRunError(..., status_code=exc.status_code)`.
- Any other `Exception` → `ScheduleRunError(...)` (no status code).

## Acceptance

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Non-stream, `payload={"model":"m","messages":[...]}` | Client built with `base_url=properties.url`; `.create()` called with `model="m"`; returns `{"result": <dump>}`. |
| 2 | Stream `payload["stream"]=True` | `.stream()` used; `get_final_completion()` assembled; same result shape as #1. |
| 3 | `effective_timeout` set | `timeout` present in `create_kwargs`. |
| 4 | Target returns `APIStatusError(status_code=429)` | `ScheduleRunError` with `status_code=429`. |
| 5 | Client is NOT constructed with `azure_deployment`/`azure_endpoint` | Assert those kwargs are absent (base_url path). |

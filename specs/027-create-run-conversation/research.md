# Phase 0 Research: Create DIAL Conversation on Run Complete

All unknowns were resolvable from the codebase and verified GET calls against a live DIAL Core dev instance plus the provided example fixtures under `conversations/`. No open NEEDS CLARIFICATION remain.

## R1. Where the `create_conversation` flag lives

- **Decision**: Add `create_conversation: bool = False` to `ScheduleProperties` (`src/lib/schemas/schedule.py`).
- **Rationale**: `ScheduleProperties` is serialized into `Schedule.kwargs` (JSONB) by `ScheduleService.create`, passed to APScheduler as job kwargs, and copied verbatim into `ScheduleRun.schedule_snapshot` at `create_in_progress`. The worker reconstructs `ScheduleProperties(**kwargs)`. Placing the flag here means it reaches the worker with **no service change and no DB migration**, and it is automatically surfaced in `ScheduleResponse.properties`.
- **Alternatives considered**: A dedicated `create_conversation` column on the `schedules` table — rejected (YAGNI): requires an Alembic migration, repository/service plumbing, and a snapshot copy, with no benefit over the JSONB field which already flows end-to-end.

## R2. Restricting the flag to chat completion schedules

- **Decision**: Extend the existing `validate_target_rules` model validator in `ScheduleProperties`: if `create_conversation` is `True` and `target_type != CHAT_COMPLETION`, raise `ValueError`.
- **Rationale**: Reuses the established validation pattern (same method already differentiates chat_completion vs rest/sse). Validation errors surface at create/update time via FastAPI 422 (FR-002, SC-004).
- **Alternatives considered**: Validating in the service layer — rejected; schema is the single source of truth and is contract-reflected.

## R3. Unified result storage for streaming and non-streaming

- **Decision**: In `ChatCompletionExecutor.execute`, always use `client.chat.completions.stream(**create_kwargs)` as an async context manager, iterate events logging only `event_type = getattr(stream_event, "type", None)`, then call `result = await stream.get_final_completion()` and store `{"result": result.model_dump()}`.
- **Rationale**: `get_final_completion()` returns an assembled `ChatCompletion` regardless of whether the payload requested streaming, giving one consistent result shape (FR-012, SC-006). The example fixtures confirm the assembled shape matches a non-streaming completion: `final_completion_*.json` (assembled) and `get_completion_*.json` (non-stream) are structurally identical (`choices[0].message.content` + optional `custom_content`). This removes the current `if isinstance(response, ChatCompletion) ... else chunks` branch.
- **Alternatives considered**: Keeping two result shapes (`{"result": ...}` vs `{"chunks": [...]}`) and normalizing at conversation-build time — rejected; pushes complexity downstream and leaves the stored result inconsistent for later inspection. The current `.create()` call is replaced by `.stream()`; both are supported by AsyncAzureOpenAI.
- **Note**: The `stream` flag in `payload` (defaulted to `False` by the validator) still controls the transport DIAL uses; `.stream()` works for both. Existing `extra_headers` and `timeout` kwargs are preserved.

## R4. Distinguishing model vs application deployments and resolving the model id

- **Decision**: Extract the deployment id from `ScheduleProperties.url` (the segment after `/deployments/`, already parsed by `_parse_deployment`). If the deployment id starts with `applications/` (the DIAL application prefix), treat it as an application: call `GET /openai/applications/{deployment_id}` and use the response `reference` as the conversation model id. Otherwise use the deployment id as-is.
- **Rationale**: Verified live — `GET /openai/applications/applications/9p6N8.../Untitled%20app%201101__0.0.1` returns `"reference": "c9e5decf-1284-421e-ab84-c790beea837d"`, which exactly matches the `model.id` in `conversations/app/dial_conversation.json`. For the model case, `conversations/gpt-5-6/dial_conversation.json` uses `model.id = "gpt-5.6-sol-2026-07-09-reasoning"` — the deployment id itself. (FR-006, FR-007, FR-008.)
- **Endpoint choice**: `GET /openai/applications/{application_name}` returned HTTP 200 with `reference`. `GET /v1/deployments/applications/...` returned 404. Use the `/openai/applications/` path.
- **Auth**: Called with the run owner's OBO token (same header the executor uses), so the lookup is owner-scoped (FR-011).
- **Alternatives considered**: Always calling the application endpoint — rejected; model deployments are not applications and the call would 404. Parsing the app reference from the run result — rejected; the result does not carry it.

## R5. Resolving the DIAL storage bucket

- **Decision**: Add `DialClient.get_bucket(api_key) -> str` calling `GET /v1/bucket` with the owner's OBO token, returning the `bucket` field.
- **Rationale**: Verified live — returns `{"bucket": "9p6N8pJg8BRJCsCs9uvb6mzwCRAe6NBJfDva5ckebXWHjirHvHYSk5MUHCzb5xjSRF"}`, matching the bucket segment in the example conversation ids. The bucket is required to build the storage path (FR-009, FR-010).
- **Alternatives considered**: Extracting bucket from `/v1/user/info` — rejected; `/v1/bucket` is the canonical, minimal source.

## R6. Conversation storage path and write endpoint

- **Decision**: Write with `PUT /v1/conversations/{bucket}/{conversation_path}` where `conversation_path = .scheduler/{schedule_id}/{run_id}`, i.e. full id `conversations/{bucket}/.scheduler/{schedule_id}/{run_id}` (FR-009). Body is the assembled `DialConversation` JSON.
- **Rationale**: Matches the stated convention and the DIAL Core API (`PUT /v1/conversations/{bucket}/{conversation_path}` exists in the OpenAPI spec). The `.scheduler/` folder namespaces scheduler-created conversations; per-`run_id` leaf guarantees no overwrite across runs (idempotency edge case).
- **Auth**: Owner's OBO token. This is a **mutating (PUT)** call — in production the worker performs it automatically with the owner's credential; during manual investigation it requires explicit user approval per the dial-api safety policy.
- **Alternatives considered**: `POST` create — the resource API is PUT-by-path (id encodes location), so PUT is correct.

## R7. Conversation document shape

- **Decision**: Build the document to mirror the DIAL Chat examples: top-level `id`, `name`, `model.id`, `messages[]` (user then assistant), plus the fields DIAL Chat sets (`prompt`, `temperature`, `folderId`, `selectedAddons`, `updatedAt`, `responseFormat`). User message: `role=user`, `content` from `payload.messages` last user prompt, `model.id`, `settings`. Assistant message: `role=assistant`, `content` and `custom_content` from `result.choices[0].message` (FR-004, FR-005).
- **Rationale**: `conversations/app/dial_conversation.json` shows the assistant message carrying `custom_content.state` (tool_execution_history) and `custom_content.stages` — these come straight from the completion's `choices[0].message.custom_content` (present in `final_completion_app.json` / `get_completion_app.json`). Copying the message object preserves both content and custom content faithfully.
- **`name`/derived fields**: `name` derived from the user prompt (as DIAL Chat does, e.g. "hi 55"); `updatedAt` = epoch millis at creation; `folderId` = `conversations/{bucket}`; `model.id` = resolved model id from R4. Minor display metadata (temperature, responseFormat) uses DIAL Chat defaults (`temperature: 1`, `responseFormat: "Markdown"`) unless present in the payload.
- **Alternatives considered**: Storing only content and dropping custom_content — rejected (violates FR-005).

## R8. Best-effort failure isolation

- **Decision**: Invoke conversation creation in `jobs.py` **after** `_persist_success`, wrapped in try/except that logs a structured error and returns. Introduce a worker-internal `ConversationCreationError` for typed internal signaling, but it is caught at the job boundary and never re-raised past run success (FR-013, SC-005). The run is already marked success before this step.
- **Rationale**: The run outcome must be immune to storage/DIAL failures. Placing the step after persistence guarantees the success record exists regardless. Logging-only surfacing was chosen by the user (2026-07-17).
- **Alternatives considered**: Creating the conversation inside the executor before persistence — rejected; couples run success to a non-essential side effect and would consume the retry policy.

## R9. HTTP client, timeout, and telemetry reuse

- **Decision**: Reuse the worker singleton `HttpClientProvider().client` and `settings.worker_http.timeout_seconds`, constructing `DialClient(client, settings.dial_url, timeout)` — the same pattern already used for OBO credentials in `_resolve_credentials`.
- **Rationale**: Constitution forbids module-level globals for reusable components and requires shared client reuse; this mirrors existing worker code exactly. Telemetry spans already wrap the job via `_tracer`.
- **Alternatives considered**: A new dedicated httpx client — rejected (resource duplication, YAGNI).

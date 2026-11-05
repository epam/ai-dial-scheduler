# Phase 0 Research: Responses Schedule Type

All Technical Context items are resolved (no NEEDS CLARIFICATION remained). This document records the decisions that shape the design.

## Decision 1 — Add `responses` as a new `TargetType` enum value

- **Decision**: Add `RESPONSES = "responses"` to `src/lib/db/enums.py::TargetType`, alongside `CHAT_COMPLETION`, `REST`, `SSE`.
- **Rationale**: `target_type` is the single execution-logic discriminator used by both validation (`ScheduleProperties.validate_target_rules`) and the worker dispatcher (`resolve_executor`). Adding a member is the minimal, idiomatic extension point and requires no DB migration (the value is stored inside JSONB `kwargs`, not a DB enum column).
- **Alternatives considered**:
  - A boolean/sub-mode flag on `chat_completion` — rejected: overloads one target type with two distinct interaction protocols and complicates validation/dispatch branching.
  - A separate top-level property — rejected: duplicates the existing discriminator and breaks the established one-enum-drives-everything pattern.

## Decision 2 — New `ResponsesExecutor` mirroring `ChatCompletionExecutor`

- **Decision**: Add `src/worker/executors/responses.py` with a `ResponsesExecutor` implementing the existing `Executor` protocol (`execute(ctx, credentials, properties) -> dict[str, Any]`). It constructs `AsyncAzureOpenAI` with a **base URL** derived from `properties.url` and the OBO api-key (stripping `Bearer `), and calls the **Responses API**.
- **IMPORTANT correction (post-implementation)**: The Responses API is **not** invoked via the Azure per-deployment URL path (`.../openai/deployments/{id}/responses`). Per the reference `res.py`, it uses `base_url=".../openai/v1/"` with the model supplied in the request payload (`model=...`). The executor therefore builds the client with `base_url=properties.url` (passed verbatim; the caller provides the base URL and the SDK appends the `responses` path) and no `azure_deployment`, letting `payload.model` flow through to `responses.stream(...)`. Deployment-URL parsing (`parse_deployment`/`extract_deployment_id`) is used only by chat completion.
- **Rationale**: The `Executor` `Protocol` (`src/worker/executors/base.py`) is the established seam; the retry/timeout orchestration (`execute_with_policy`) and error mapping already work against it. Reusing `strip_bearer_prefix`, `HttpClientProvider`, and `ScheduleRunError` mapping keeps behavior identical to chat completion for everything except the SDK call and URL/model wiring.
- **Reference**: `res.py` demonstrates the SDK surface:
  - Non-streaming: `await client.responses.create(model=..., input=..., stream=False)`
  - Streaming: `async with client.responses.stream(model=..., input=...) as s: async for event in s: ...; await s.get_final_response()`
- **Alternatives considered**:
  - Parameterizing `ChatCompletionExecutor` to switch APIs — rejected (Simplicity/YAGNI): two SDK resources with different request/response shapes; a second small executor is clearer than a branching one.

## Decision 3 — Assemble a single final Response for both stream and non-stream

- **Decision**: The `stream` flag from `properties.payload` (normalized to default `False`) selects the SDK path. When `stream` is true, use `client.responses.stream(...)` + `get_final_response()`; otherwise use `client.responses.create(...)` (non-streaming). Both store `{"result": response.model_dump()}`, so the result shape is identical. This branching applies equally to chat completion (`chat.completions.create` vs `chat.completions.stream` + `get_final_completion()`).
- **Rationale**: FR-003/SC-003 require the same stored result shape regardless of transport. Using `.create()` for the non-streaming default avoids opening a streaming connection when the caller didn't ask for one, matching DIAL's `stream` semantics. `res.py` confirms both `responses.create(...)` and `responses.stream(...)` exist.
- **Alternatives considered**:
  - Always streaming (ignore the flag, assemble via `get_final_response()`) — rejected: opens a streaming connection even for non-streaming schedules; honoring the flag is the correct default and matches chat completion.
  - Storing raw stream events — rejected: not a single assembled shape; breaks inspection parity.

## Decision 4 — Reuse chat-completion validation rules for `responses`

- **Decision**: In `ScheduleProperties.validate_target_rules`, treat `responses` under the same branch as `chat_completion`: `method` must be unset, `api_version` is required, and `payload["stream"]` defaults to `False` when absent. Additionally, `responses` **requires `payload.model`** (the Responses URL carries no deployment segment, so the model identifies the deployment). `create_conversation` remains allowed **only** for `chat_completion` and is rejected for `responses` (and for `rest`/`sse`).
- **Rationale**: The spec states `responses` is "same to chat completion" for validation. Grouping the two target types in the existing validator is the smallest change and preserves the current error messages/semantics. Keeping `create_conversation` chat-completion-only matches the Out-of-Scope decision (FR-009).
- **Alternatives considered**:
  - Separate validation function for `responses` — rejected (DRY): the rules are identical; a shared branch avoids divergence.
  - Allowing `create_conversation` for `responses` now — rejected: conversation builder currently assumes a chat-completion result shape (`choices[0].message`); Responses output shape differs. Out of scope per spec.

## Decision 5 — Payload input shape carried through unchanged

- **Decision**: The Responses request input (e.g. `input`, `instructions`, and other Responses parameters) is supplied by the caller inside `properties.payload` and passed through to `responses.create/stream` as `**payload` (minus the extracted `stream` flag), with `extra_headers` applied — mirroring how `ChatCompletionExecutor` forwards `payload`.
- **Rationale**: Keeps the scheduler agnostic to the exact Responses request schema (which DIAL/OpenAI own) and matches the existing chat-completion pass-through contract. No scheduler-side schema for the Responses body is needed (YAGNI).
- **Alternatives considered**:
  - Modeling the Responses request body as a Pydantic schema in the scheduler — rejected: the scheduler does not model the chat completion body either; it forwards `payload`. Adding a model here would be premature and coupling to an external schema.

## Cross-cutting confirmations

- **No migration**: `target_type` and `properties` are serialized into the schedule's `kwargs` (JSONB); adding an enum value and validation branch requires no Alembic migration.
- **Timeout/retry/tracking parity**: `execute_with_policy` and `jobs.py` are target-type-agnostic (they call `resolve_executor(properties.target_type)`), so triggering, timeout, retry, run tracking, and pause/resume apply automatically once the executor is registered.
- **Observability**: The executor logs `stream_event` with `event_type` only (never content), matching `ChatCompletionExecutor` and FR-010.

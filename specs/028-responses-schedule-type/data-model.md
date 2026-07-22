# Phase 1 Data Model: Responses Schedule Type

No database schema changes. Target type and properties are serialized into the schedule's JSONB `kwargs` column; there is no DB enum column for `target_type`. This document describes the affected in-code entities.

## TargetType (enum) — `src/lib/db/enums.py`

Add one member:

| Member | Value | Meaning |
|--------|-------|---------|
| `CHAT_COMPLETION` | `chat_completion` | Chat Completions API interaction (existing) |
| `REST` | `rest` | Generic REST call (existing) |
| `SSE` | `sse` | Server-sent events call (existing) |
| **`RESPONSES`** | **`responses`** | **Responses API interaction (new)** |

## ScheduleProperties — `src/lib/schemas/schedule.py`

No new fields. The existing model gains `responses` as an accepted `target_type` and applies the chat-completion validation branch to it.

**Fields (unchanged):** `target_type`, `method`, `url`, `payload`, `extra_headers`, `api_version`, `timeout`, `retry`, `create_conversation`.

**Validation rules for `target_type == RESPONSES`** (mostly identical to `CHAT_COMPLETION`):

| Rule | Behavior |
|------|----------|
| `method` | MUST be unset (`None`); a supplied method raises `ValueError`. |
| `api_version` | REQUIRED; missing raises `ValueError`. |
| `payload["stream"]` | Defaults to `False` when absent (payload normalized). |
| `payload["model"]` | REQUIRED for `responses`; missing/empty raises `ValueError`. The Responses API URL carries no deployment path segment, so the deployment is identified by the payload model. |
| `create_conversation` | MUST remain `False` for `responses`; enabling it raises `ValueError` ("create_conversation is only allowed for target_type 'chat_completion'"). |
| `url` | MUST be a valid HTTP(S) URL (existing shared validator). Expected form: the Responses base URL (e.g. `.../openai/v1`), passed to the client verbatim; not a per-deployment path. |
| `timeout` | If set, MUST be positive (existing shared validator). |

**Validation rules for `rest`/`sse` (unchanged):** `method` required, `api_version` forbidden, `create_conversation` forbidden.

### State / flow

`target_type` is a pure discriminator with no state transitions. It drives:
1. **Validation** — which branch of `validate_target_rules` applies.
2. **Dispatch** — which `Executor` `resolve_executor` returns.

## Run result shape (stored in `schedule_runs.result`)

For `responses` runs the executor stores:

```json
{ "result": { /* Response.model_dump() from the OpenAI responses API */ } }
```

This mirrors the chat-completion contract (`{"result": ChatCompletion.model_dump()}`), assembled identically for streaming (`get_final_response()`) and non-streaming (`responses.create(stream=False)`) runs. The scheduler does not model the Response body's internal fields — it stores the SDK's `model_dump()` verbatim.

## Entities NOT changed

- `Schedule` ORM model / `schedule_runs` table — no columns added; `kwargs`/`result` are JSONB.
- `RunContext`, `OboCredentials`, retry/timeout orchestration — target-type-agnostic, reused as-is.
- Conversation entities (`DialConversation`, etc.) — out of scope; `responses` does not create conversations.

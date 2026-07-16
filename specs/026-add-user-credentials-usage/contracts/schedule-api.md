# Contract: Schedule API `service_id` additions

Published via FastAPI OpenAPI (and thus surfaced through MCP).

## Create — `POST /schedules/`

`service_id` becomes a **required** top-level field on the request body.

```json
{
  "display_name": "Daily GPT-4o summary",
  "service_id": "dial-oauth",
  "trigger": { "cron": { "fields": { "hour": 9, "minute": 0 } } },
  "properties": {
    "target_type": "chat_completion",
    "url": "http://dial-core.dial.svc.cluster.local/openai/deployments/gpt-4o/chat/completions",
    "payload": { "messages": [{ "role": "user", "content": "Summarise today." }], "model": "gpt-4o" },
    "api_version": "2025-01-01-preview"
  }
}
```

- Omitting `service_id` → `422 Unprocessable Entity` (required, non-empty).
- MCP guidance (endpoint docstring) instructs clients to use `"dial-oauth"` unless another
  external service is required.

## Update — `PUT /schedules/{schedule_id}`

Same request body shape; `service_id` required (full replace semantics, consistent with the
existing update behavior).

## Responses — Create / Update / Get (`ScheduleResponse`) and List (`ScheduleListItem`)

Add `service_id` to the returned representation:

```json
{
  "id": "…",
  "display_name": "…",
  "service_id": "dial-oauth",
  "next_run_time": "…",
  "trigger": { "…": "…" },
  "properties": { "…": "…" },
  "metadata": {},
  "created_by": "…",
  "created_at": "…",
  "updated_at": "…"
}
```

## Backward compatibility

- Existing stored schedules are backfilled to `service_id = "dial-oauth"` and therefore serialize
  successfully in responses.
- This is an additive, required-field change to the request contract; per Constitution IV it is a
  breaking change for API clients that create/update schedules (they must now send `service_id`).
  Callers relying only on read endpoints are unaffected.

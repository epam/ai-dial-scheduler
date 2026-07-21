# Contract: ScheduleProperties `create_conversation`

Change to the public API contract (reflected in FastAPI OpenAPI via the Pydantic schema).

## Request/response field

Added to `ScheduleProperties` (used in `POST /schedules/` and `PUT /schedules/{id}` request bodies, and returned in `ScheduleResponse.properties`).

```jsonc
{
  "target_type": "chat_completion",
  "url": "https://core.aks.dev.dial.parts/openai/deployments/gpt-5.6-sol-2026-07-09-reasoning/chat/completions",
  "api_version": "2024-02-01",
  "payload": { "messages": [{ "role": "user", "content": "hi" }], "stream": false },
  "create_conversation": true    // NEW — optional, default false
}
```

## Rules

| Condition | Result |
|-----------|--------|
| `create_conversation` omitted | Defaults to `false`. |
| `create_conversation: true` + `target_type == "chat_completion"` | Accepted. |
| `create_conversation: true` + `target_type in {"rest","sse"}` | **422** validation error: `create_conversation is only allowed for target_type 'chat_completion'`. |
| `create_conversation: false` + any target type | Accepted (no-op). |

## Backward compatibility

Additive, optional field with a default → non-breaking. No contract major-version bump. Existing schedules (no field) behave as `create_conversation: false`.

## Visibility

`ScheduleResponse.properties` is a passthrough of stored `kwargs`, so the flag appears automatically in Create/Update/Detail responses (FR-014). No change to `ScheduleListItem`.

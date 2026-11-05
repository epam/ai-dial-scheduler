# Contract: Run Config (`schedules.kwargs` → `ScheduleProperties`)

The authoritative run config the worker validates on every fire. Stored as JSONB in
`schedules.kwargs`, mirrors the creator API's `properties` field, validated by the existing
`src/lib/schemas/schedule.py::ScheduleProperties`. The worker re-validates on read because the
structure is enforced only by the creator API, not the database.

## Shape

```json
{
  "target_type": "chat_completion | rest | sse",
  "method":      "POST | PUT | GET | null",
  "url":         "<string>",
  "payload":     {},
  "extra_headers": { "<header>": "<value>" },
  "api_version": "<string> | null"
}
```

| Field | Type | Validation rule |
|-------|------|-----------------|
| `target_type` | enum | One of `chat_completion`, `rest`, `sse`. Selects the executor. |
| `method` | enum \| null | **Must be null/unset** for `chat_completion` (ignored). **Required** for `rest`/`sse`. |
| `url` | string | Valid HTTP URL. For `chat_completion`, a standard OpenAI/Azure deployment URL. |
| `payload` | json | Sent as request body / chat args. For `chat_completion`, `stream` defaults to `false` when absent. |
| `extra_headers` | dict[str,str] | Merged over injected headers; **wins on collision**. |
| `api_version` | string \| null | Allowed **only** for `chat_completion`. |

Any deviation from these rules ⇒ validation failure ⇒ `ScheduleRunError`.

## Per-target header injection

| Target | Injected header | Collision rule |
|--------|-----------------|----------------|
| `chat_completion` | `api_key` handled by `AsyncAzureOpenAI` (from `settings.dial_api_key`) | `extra_headers` passed through `extra_headers=`. |
| `rest` | `Api-Key: <settings.dial_api_key>` | `extra_headers` win. |
| `sse` | `Api-Key: <settings.dial_api_key>` + `Accept: text/event-stream` | `extra_headers` win. |

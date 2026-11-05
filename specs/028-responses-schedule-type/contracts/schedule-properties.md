# Contract: ScheduleProperties — `responses` target type

**Applies to**: `ScheduleProperties` (`src/lib/schemas/schedule.py`), reflected in the OpenAPI schema for Create/Update schedule endpoints. No endpoint signature changes — the enum addition flows through automatically.

## `target_type` enum

`target_type` accepts a new value: `"responses"` (in addition to `chat_completion`, `rest`, `sse`).

## Request example (Create schedule with `responses` type)

```json
{
  "display_name": "Daily responses summary",
  "service_id": "my-service",
  "trigger": { "cron": { "fields": { "hour": "9", "minute": "0" } } },
  "properties": {
    "target_type": "responses",
    "url": "https://core.example.dial/openai/deployments/gpt-5.6-luna-2026-07-09/responses",
    "api_version": "2025-01-01-preview",
    "payload": { "input": "Summarize yesterday's incidents", "stream": false }
  },
  "metadata": {}
}
```

## Validation rules (MUST)

| Condition | Result |
|-----------|--------|
| `target_type == "responses"` and `method` is set | **422** — `method must not be set for target_type 'responses'` |
| `target_type == "responses"` and `api_version` missing/empty | **422** — `api_version is required for target_type 'responses'` |
| `target_type == "responses"` and `payload.stream` absent | Accepted; `payload.stream` normalized to `false` |
| `target_type == "responses"` and `create_conversation == true` | **422** — `create_conversation is only allowed for target_type 'chat_completion'` |
| `url` not a valid HTTP(S) URL | **422** — URL validation error |
| `timeout` set and `<= 0` | **422** — timeout must be positive |

> Error messages mirror the existing chat-completion messages, with the target type name substituted where the current code interpolates `self.target_type`. The `method`/`api_version` messages currently hardcode `'chat_completion'`; implementation may either share the exact chat-completion messages for the grouped branch or interpolate the concrete target type — tests assert the presence of the rule, not the exact wording, except for the `create_conversation` message which stays chat-completion-specific.

## Read responses

`ScheduleResponse.properties` and `ScheduleListItem` expose `target_type: "responses"` unchanged (the value is echoed from stored `kwargs`), so users can distinguish `responses` schedules (FR-008).

## Backward compatibility

Additive only. Existing `chat_completion`/`rest`/`sse` schedules are unaffected; no migration; no breaking change to the schema contract (new enum value is a minor, non-breaking addition).

# Contract: ScheduleProperties validation — chat_completion base URL + payload.model

**Feature**: `029-base-url-model-field`

Applies to `ScheduleProperties` (`src/lib/schemas/schedule.py`), validated before each schedule
create/update and before each run start.

## chat_completion rules (after this feature)

Given `target_type == "chat_completion"`:

- `method` MUST be unset → else `ValueError`. *(unchanged)*
- `api_version` MUST be non-empty → else `ValueError`. *(unchanged)*
- `stream` is injected into `payload` as `False` when absent. *(unchanged)*
- **`payload.model` MUST be present and non-empty → else `ValueError`.** *(new — was responses-only)*
- `url` MUST be a valid HTTP URL. It is treated as a **base URL**; no `/deployments/{name}/...` path
  segment is required or parsed.
- `create_conversation` MAY be `true`. *(unchanged — allowed only for chat_completion)*

## responses rules (unchanged)

Given `target_type == "responses"`: identical to chat_completion except `create_conversation` MUST be
`false`. `payload.model` was already required and remains required.

## Acceptance

| # | Input | Expected |
|---|-------|----------|
| 1 | chat_completion, base URL, `payload={"model":"gpt-4","messages":[...]}`, `api_version` set | Valid; `stream` defaulted to `False`. |
| 2 | chat_completion, `payload` without `model` | `ValueError` — `payload.model is required...`. |
| 3 | chat_completion, `payload={"model":""}` | `ValueError` — empty model rejected. |
| 4 | chat_completion, `method` set | `ValueError` — method must not be set. *(unchanged)* |
| 5 | chat_completion, `api_version` missing | `ValueError` — api_version required. *(unchanged)* |
| 6 | responses, `payload` without `model` | `ValueError`. *(unchanged)* |
| 7 | responses, `create_conversation=true` | `ValueError`. *(unchanged)* |

## OpenAPI impact

The `ScheduleProperties` JSON shape is unchanged (`payload` remains a free-form object). The
`payload.model` requirement is a semantic validation constraint, not a schema field change.

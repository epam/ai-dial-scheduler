# Data Model: Base URL and Payload Model for Chat Completion

**Feature**: `029-base-url-model-field` | **Date**: 2026-07-22

No new entities, tables, columns, or migrations. This feature changes the **interpretation and
validation** of existing fields on `ScheduleProperties`. The entities below are existing; only the
noted rules change.

## Entity: ScheduleProperties (existing — `src/lib/schemas/schedule.py`)

Target-call configuration stored in the schedule's JSONB `kwargs`. Unchanged field set:

| Field | Type | Notes (this feature) |
|-------|------|----------------------|
| `target_type` | `TargetType` | Unchanged. This feature affects `chat_completion`. |
| `url` | `str` (HTTP URL) | **Reinterpreted** for `chat_completion`: now a **base URL** (e.g. `.../openai/v1`), not a per-deployment path (`.../openai/deployments/{name}/chat/completions`). Validation stays "valid HTTP URL"; no path-segment requirement. |
| `payload` | `dict[str, Any]` | **New rule** for `chat_completion`: MUST contain a non-empty `model`. `payload.model` selects the deployment. |
| `api_version` | `str \| None` | Unchanged — required for `chat_completion` and `responses`. |
| `extra_headers` | `dict[str,str]` | Unchanged. |
| `create_conversation` | `bool` | Unchanged — allowed only for `chat_completion`. |
| `method`, `timeout`, `retry` | — | Unchanged. |

### Validation rules (`validate_target_rules`, mode="after")

Change: the `payload.model` requirement, currently applied only when
`target_type == RESPONSES`, is extended to also cover `CHAT_COMPLETION`.

| Rule | chat_completion (before) | chat_completion (after) | responses |
|------|--------------------------|-------------------------|-----------|
| `method` must be unset | ✅ | ✅ (unchanged) | ✅ |
| `api_version` required | ✅ | ✅ (unchanged) | ✅ |
| `stream` defaults to `False` | ✅ | ✅ (unchanged) | ✅ |
| `payload.model` required | ❌ (not required) | ✅ **(new)** | ✅ |
| `create_conversation` allowed | ✅ | ✅ (unchanged) | ❌ |

**Error message**: reuse a single clear message covering both types, e.g.
`"payload.model is required for target_type '{target_type}'"`.

## Entity: ConversationBuildInput (existing — `src/lib/schemas/conversation.py`)

Unchanged shape (`model_id`, `bucket`, `schedule_id`, `run_id`). Only the **source** of `model_id`
changes upstream: it is derived from `payload.model` (then application-reference-resolved when the id
starts with `APPLICATION_DEPLOYMENT_PREFIX`) instead of from `extract_deployment_id(url)`.

## Removed code (not entities)

- `parse_deployment(url)` and `extract_deployment_id(url)` in `src/lib/utils/dial.py` are removed
  (no remaining callers). `strip_bearer_prefix` remains.

## State transitions

None. Run lifecycle, retry, and persistence are unchanged.

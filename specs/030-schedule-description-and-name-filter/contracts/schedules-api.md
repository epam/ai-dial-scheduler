# API Contract: Schedule Description & Name Filter

Additive, backward-compatible changes to the existing `/schedules` endpoints. No version bump (new optional request field, new response fields, new optional query parameter). Contracts surface automatically via FastAPI OpenAPI; this document is the human-readable delta.

## Schema changes

### `ScheduleRequest` (create/update body) — MODIFIED

New optional field:

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `description` | `string \| null` | No (default `null`) | `max_length = 1000` characters |

- Supplying `description` longer than 1000 characters ⇒ **422 Unprocessable Entity** with a Pydantic validation error; schedule is not created/modified.
- Omitting `description` ⇒ stored as `null`.
- On **update**, the supplied `description` fully replaces the prior value (omitting it sets `null`, consistent with full-replacement update semantics).

### `ScheduleResponse` (create/update/details response) — MODIFIED

New field:

| Field | Type | Notes |
|-------|------|-------|
| `description` | `string \| null` | Stored description; `null` when absent. |

### `ScheduleListItem` (list row) — MODIFIED

New fields:

| Field | Type | Notes |
|-------|------|-------|
| `description` | `string \| null` | Stored description; `null` when absent. |
| `trigger` | `object` | Full trigger definition, identical to `ScheduleResponse.trigger` on details. Added **alongside** the existing `trigger_type` (which is retained). |

## Endpoint changes

### `GET /schedules/` — MODIFIED

New optional query parameter:

| Param | Type | Default | Behavior |
|-------|------|---------|----------|
| `name` | `string` | absent | Case-insensitive **substring** match against the schedule's `display_name`. Empty/whitespace ⇒ ignored (no filter). LIKE wildcards (`%`, `_`) in the term are matched literally. |

Composition:
- `name` composes with existing `metadata__<key>=<value>` filters and owner scope using **AND**.
- Filtering is applied before pagination; `count`/paging reflect only matching schedules.
- No `name` ⇒ identical behavior to today (plus the new `description` field on each row).

Unchanged: `POST /schedules/`, `PUT /schedules/{id}`, `GET /schedules/{id}`, pause/resume — except that create/update/detail now accept/return `description`.

## Example requests

### Create with description

```http
POST /schedules/
Content-Type: application/json

{
  "display_name": "Daily GPT-4o summary",
  "description": "Sends the ops channel a daily highlight digest at 09:00.",
  "service_id": "dial-oauth",
  "trigger": {"cron": {"fields": {"hour": 9, "minute": 0}}},
  "properties": {
    "target_type": "chat_completion",
    "url": "http://dial-core.dial.svc.cluster.local/openai/",
    "payload": {"messages": [{"role": "user", "content": "Summarise."}], "model": "gpt-4o"},
    "api_version": "2025-01-01-preview"
  }
}
```

Response `201` includes `"description": "Sends the ops channel a daily highlight digest at 09:00."`.

### Reject over-length description

```http
POST /schedules/    # description = 1001 chars
→ 422 Unprocessable Entity
{ "detail": [ { "loc": ["body", "description"], "type": "string_too_long", ... } ] }
```

### List filtered by name (composes with metadata + pagination)

```http
GET /schedules/?name=summary&metadata__tenant=hcscd&limit=20&offset=0
```

Returns only owner-scoped schedules whose `display_name` contains "summary" (case-insensitive) **and** whose metadata contains `tenant=hcscd`, paginated. Each row includes `description`.

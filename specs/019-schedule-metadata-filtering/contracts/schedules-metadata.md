# API Contract: Schedule Metadata & Filtering

All endpoints are owner-scoped (the authenticated user id, via `get_user_id`). Only fields/behaviors **changed by this feature** are described; existing fields are unchanged. The authoritative machine-readable contract is the FastAPI auto-generated OpenAPI document.

## Changed schemas

### ScheduleRequest (request body) — `metadata` added

```jsonc
{
  "display_name": "Daily GPT-4o summary",
  "trigger": { "cron": { "fields": { "hour": 9, "minute": 0 } } },
  "properties": { /* unchanged */ },
  "metadata": { "tenant": "hcscd", "env": "prod" }   // NEW, optional; default {}
}
```

- `metadata`: object (map of string keys → any JSON value). Optional. Omitted ⇒ `{}`.
- Used by `POST /schedules/` and `PUT /schedules/{schedule_id}`.
- On update, `metadata` fully **replaces** the prior value (no merge).

### ScheduleResponse (response body) — `metadata` added

```jsonc
{
  "id": "…",
  "display_name": "…",
  "next_run_time": "2026-06-18T09:00:00Z",
  "trigger": { /* … */ },
  "properties": { /* … */ },
  "metadata": { "tenant": "hcscd", "env": "prod" },   // NEW; always an object
  "created_by": "…",
  "created_at": "…",
  "updated_at": "…"
}
```

- Returned by `POST /schedules/`, `PUT /schedules/{id}`, and `GET /schedules/{id}`.
- `metadata` is always present and always an object (never `null`); empty when none stored.

### ScheduleListItem — unchanged

No `metadata` field is added to list rows in this release.

## Changed endpoint behavior

### GET /schedules/ — metadata filtering (NEW query parameters)

Accepts zero or more dynamic query parameters of the form:

```
metadata__<key>=<value>
```

**Examples**

| Request | Returns |
|---------|---------|
| `GET /schedules/?metadata__tenant=hcscd` | Owner's schedules whose `metadata.tenant` is the string `"hcscd"`. |
| `GET /schedules/?metadata__tenant=hcscd&metadata__env=prod` | Owner's schedules matching **both** pairs (AND). |
| `GET /schedules/?limit=10&offset=0&metadata__tenant=hcscd` | Filtered + paginated. |
| `GET /schedules/` | Unfiltered owner-scoped page (unchanged behavior). |

**Matching rules**

- Match is on **string** values only: `metadata__count=5` does **not** match a schedule storing `{"count": 5}` (number), only one storing `{"count": "5"}` (string).
- Exact, full-value, case-sensitive equality. No substring/prefix matching.
- Multiple `metadata__*` params are combined with **AND**.
- The key is the substring after the first `metadata__` prefix (keys may contain `__`).
- A filter referencing a key no owned schedule has ⇒ empty `results`, HTTP 200 (not an error).
- Filtering composes with `limit`/`offset`; `count` reflects the filtered total.

**Response**: unchanged `Page[ScheduleListItem]` envelope:

```jsonc
{
  "count": 1,
  "limit": 20,
  "offset": 0,
  "results": [ { "id": "…", "display_name": "…", "next_run_time": "…",
                 "trigger_type": "cron", "created_by": "…",
                 "created_at": "…", "updated_at": "…" } ],
  "next": null,
  "previous": null
}
```

**Status codes**: `200 OK` for valid requests including unmatched filters; existing auth behavior unchanged. No new error responses are introduced by filtering.

# API Contract: Schedule Management Endpoints (v1)

Source of truth derived from `documentation/api_contract.md` and `documentation/properties.md`.
All routes are ownership-scoped to `created_by` except where noted. This iteration uses the
fixed identity `MOCK_USER_ID = "mock_user_id"` for both storage and filtering. FastAPI
generates the live OpenAPI document; this file is the human-readable contract.

Conventions:
- Base path: `/schedules` (mounted on the v1 API surface). `redirect_slashes=False`.
- Timestamps are ISO-8601 with timezone (UTC).
- Validation errors return **422** with FastAPI's standard error body. Nothing is persisted on
  a validation failure.

## Shared objects

### `trigger` (request) — exactly one of `cron` / `date`

```json
{
  "cron": {
    "fields": {
      "year": "str|int|null", "month": "str|int|null", "day": "str|int|null",
      "week": "str|int|null", "day_of_week": "str|int|null", "hour": "str|int|null",
      "minute": "str|int|null", "second": "str|int|null"
    },
    "start_date": "datetime|null",
    "end_date": "datetime|null"
  },
  "date": "datetime|null"
}
```

Rules: providing zero or both of `cron`/`date` → 422.

### `properties` (request)

```json
{
  "target_type": "chat_completion|rest|sse",
  "method": "POST|PUT|GET|null",
  "url": "https://...",
  "payload": {},
  "extra_headers": {"tenant_id": "hcscd"},
  "api_version": "str|null"
}
```

Rules (422 on violation):
- `chat_completion`: `method` not used; `url` must be a valid OpenAI-compatible deployment URL;
  `payload.stream` defaults to `false` when absent; `api_version` applicable.
- `rest` / `sse`: `method` required (`POST`/`PUT`/`GET`); `api_version` not used.

---

## 1. Create Schedule

`POST /schedules/` → **201 Created**

**Request body**

```json
{
  "display_name": "Daily report",
  "trigger": { "cron": { "fields": { "hour": "9", "minute": "0" } } },
  "properties": {
    "target_type": "chat_completion",
    "method": null,
    "url": "https://core.dial/openai/deployments/gpt-4o/chat/completions",
    "payload": { "messages": [{ "role": "user", "content": "hi" }] },
    "extra_headers": { "tenant_id": "hcscd" },
    "api_version": "2024-02-01"
  }
}
```

**Behavior**: generate `id = str(uuid4())`; insert `schedules` row (`created_by = mock_user_id`)
and commit; `add_job(id=id, func="src.worker.jobs:execute_scheduled_job", trigger=<built>,
kwargs=properties ∪ {schedule_id: id}, replace_existing=True)` off the event loop; on
`add_job` failure delete the row and return **502**. `next_run_time` read from `apscheduler_jobs`.

**Response 201**

```json
{
  "id": "5f1c…", "display_name": "Daily report",
  "next_run_time": "2026-06-12T09:00:00+00:00",
  "trigger": { "...": "echoed validated trigger" },
  "properties": { "...": "echoed validated properties" },
  "created_by": "mock_user_id",
  "created_at": "2026-06-11T12:00:00+00:00",
  "updated_at": "2026-06-11T12:00:00+00:00"
}
```

| Status | When |
|--------|------|
| 201 | created + registered |
| 422 | trigger/properties validation failed (nothing persisted) |
| 502 | `add_job` failed; row rolled back |

---

## 2. Update Schedule

`PUT /schedules/{schedule_id}` → **200 OK**

**Request body**: identical shape to Create.

**Behavior**: load owned row (else 404, capturing prior values); update fields and commit;
`add_job(id=schedule_id, replace_existing=True, …)` off the event loop; on failure restore the
prior values and return **502**. `next_run_time` read from `apscheduler_jobs`.

**Response 200**: same object as Create's 201 body (with refreshed `updated_at`).

| Status | When |
|--------|------|
| 200 | updated + re-registered |
| 404 | id missing or not owned by caller |
| 422 | validation failed (row unchanged) |
| 502 | `add_job` failed; row restored |

---

## 3. List Schedules

`GET /schedules/?limit={limit}&offset={offset}` → **200 OK**

Query params: `limit` (default 20), `offset` (default 0). Scoped to caller's `created_by`.

**Response 200**

```json
{
  "count": 242,
  "limit": 20,
  "offset": 40,
  "results": [
    {
      "id": "5f1c…",
      "next_run_time": "2026-06-12T09:00:00+00:00",
      "trigger_type": "cron",
      "created_by": "mock_user_id",
      "created_at": "2026-06-11T12:00:00+00:00",
      "updated_at": "2026-06-11T12:00:00+00:00"
    }
  ],
  "next": "/schedules?limit=20&offset=60",
  "previous": "/schedules?limit=20&offset=20"
}
```

Rules: `previous` is `null` on the first page; `next` is `null` on the last page. A schedule
with no live job appears with `next_run_time: null`.

| Status | When |
|--------|------|
| 200 | page returned (possibly empty `results`) |

---

## 4. Schedule Details

`GET /schedules/{schedule_id}` → **200 OK**

Scoped to caller's `created_by`.

**Response 200**: identical object shape to Create's response body.

| Status | When |
|--------|------|
| 200 | owned schedule returned |
| 404 | id missing or not owned by caller |

---

## Out of scope (not implemented this feature)

`DELETE /schedules/{schedule_id}`, `POST /schedules/{id}/pause`, `POST /schedules/{id}/resume`,
`GET /schedules/{id}/runs`, `GET /schedules/{id}/runs/{run_id}`.

All endpoints except Insert Schedule are filtered by created_by, so only creator can read/update schedules.

## Insert Schedule
POST /schedules/

Request
```json
{
  "display_name": "",
  "service_id": "dial-oauth",
  "trigger": {
    "cron": {
      "fields": {
        "year": "str|int|None",
        "month": "str|int|None",
        "day": "str|int|None",
        "week": "str|int|None",
        "day_of_week": "str|int|None",
        "hour": "str|int|None",
        "minute": "str|int|None",
        "second": "str|int|None"
      },
      "start_date": "datetime|None",
      "end_date": "datetime|None"
    },
    "date": "datetime|None"
  },
  "properties": {
    "target_type": "chat_completion|rest|sse",
    "method": "POST|PUT|GET|null",
    "url": "",
    "payload": {},
    "extra_headers": {"tenant_id": "hcscd"},
    "api_version": "str",
    "create_conversation": "bool"
  }
}
```

Response
```json
{
  "id": "str",
  "display_name": "",
  "service_id": "dial-oauth",
  "next_run_time": "datetime|None",
  "trigger": {},
  "properties": {},
  "created_by": "",
  "created_at": "datetime",
  "updated_at": "datetime"
}
```
ID is generated uuid4 converted to string, same value used for schedules.id table and as job id in scheduler.add_job
Table populated with created_by = user `sub` claim from /v1/info dial endpoint (resolved via
`USER_ID_PATH_LIST`, default `["sub", "userClaims.sub.0", "project"]`).

`service_id` is **required**. It identifies the DIAL external service (see DIAL's
external-service sign-in feature) whose on-behalf-of (OBO) credentials authenticate every
execution of this schedule — e.g. `dial-oauth` or `dial-api-key`. Before each run, the worker
fetches OBO credentials for the schedule's owner (`created_by`) and `service_id`; if the owner
has not signed in to that service, the run fails with a credentials-stage error (see Run Details
below) and the target request is never sent. Existing schedules created before this field existed
were backfilled with `service_id = "dial-oauth"`.

## Update Schedule
PUT /schedules/{schedule_id}

Request
```json
{
  "display_name": "",
  "service_id": "dial-oauth",
  "trigger": {
    "cron": {
      "fields": {
        "year": "str|int|None",
        "month": "str|int|None",
        "day": "str|int|None",
        "week": "str|int|None",
        "day_of_week": "str|int|None",
        "hour": "str|int|None",
        "minute": "str|int|None",
        "second": "str|int|None"
      },
      "start_date": "datetime|None",
      "end_date": "datetime|None"
    },
    "date": "datetime|None"
  },
  "properties": {
    "target_type": "chat_completion|rest|sse",
    "method": "POST|PUT|GET|null",
    "url": "",
    "payload": {},
    "extra_headers": {"tenant_id": "hcscd"},
    "api_version": "str",
    "create_conversation": "bool"
  }
}
```

Response
```json
{
  "id": "str",
  "display_name": "",
  "service_id": "dial-oauth",
  "next_run_time": "datetime|None",
  "trigger": {},
  "properties": {},
  "created_by": "",
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

## List Schedules
GET /schedules/

```json
{
  "count": 242,
  "limit": 20,
  "offset": 40,
  "results": [
    {
      "id": "str",
      "display_name": "",
      "service_id": "dial-oauth",
      "next_run_time": "datetime|None",
      "trigger_type": "cron|date",
      "created_by":"user_id",
      "created_at": "datetime",
      "updated_at": "datetime"
    }
  ],
  "next": "/schedules?limit=20&offset=60",
  "previous": "/schedules?limit=20&offset=20"
}
```


## Schedule Details
GET /schedules/{schedule_id}


```json
{
  "id": "str",
  "display_name": "",
  "service_id": "dial-oauth",
  "next_run_time": "datetime|None",
  "trigger": {},
  "properties": {},
  "created_by": "",
  "created_at": "datetime",
  "updated_at": "datetime"
}
```


## Delete Schedule
TODO: discuss if we really want to delete something or just pause schedule and mark it as deleted, storing runs.
DELETE /schedules/{schedule_id}


## Pause/Resume schedule
POST /schedules/{schedule_id}/pause
POST /schedules/{schedule_id}/resume


## Schedule run list

GET /schedules/{schedule_id}/runs

```json
{
  "count": 242,
  "limit": 20,
  "offset": 40,
  "results": [
    {
      "id": "uuid",
      "status": "success|error|in_progress|missed",
      "start_time": "datetime",
      "end_time": "datetime|none"
    }
  ],
  "next": "/schedules/{schedule_id}/runs?limit=20&offset=60",
  "previous": "/schedules/{schedule_id}/runs?limit=20&offset=20"
}
```

## Run details

GET /schedules/{schedule_id}/runs/{run_id}

```json
{
  "id": "uuid",
  "status": "success|error|in_progress|missed",
  "start_time": "datetime",
  "end_time": "datetime|none",
  "result": {},
  "schedule": {
    "display_name": "",
    "trigger": {},
    "properties": {}
  }
}
```

For a failed run, `result` distinguishes *why* it failed via a `stage` key:
- `{"stage": "credentials", "error_text": "...", "credentials_response": {...}}` — the worker
  could not obtain on-behalf-of credentials for the schedule's owner/`service_id` before dispatch
  (e.g. the owner has not signed in to the external service); `credentials_response` carries the
  DIAL lookup response. The target endpoint was never called and no retries were attempted.
- `{"error_text": "..."}` (no `stage` key) — the target request itself failed after credentials
  were successfully obtained; this is the pre-existing shape and follows the schedule's retry
  policy.

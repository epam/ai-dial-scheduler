All endpoints except Insert Schedule are filtered by created_by, so only creator can read/update schedules.

## Insert Schedule
POST /schedules/

Request
```json
{
  "display_name": "",
  "service_id": "dial-native",
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
  "service_id": "dial-native",
  "next_run_time": "datetime|None",
  "trigger": {},
  "properties": {},
  "is_deleted": false,
  "created_by": "",
  "created_at": "datetime",
  "updated_at": "datetime"
}
```
ID is generated uuid4 converted to string, same value used for schedules.id table and as job id in scheduler.add_job
Table populated with created_by = user `sub` claim from /v1/info dial endpoint (resolved via
`USER_ID_PATH_LIST`, default `["sub", "userClaims.sub.0", "project"]`).

`service_id` is **required**. It identifies the DIAL external service whose on-behalf-of (OBO)
credentials authenticate every execution of this schedule — `dial-native`, a DIAL-native service
backed by platform-wide offline credentials. Before each run, the worker fetches OBO credentials
for the schedule's owner (`created_by`) and `service_id`; if the owner has not enabled offline
access, or an administrator has not approved this application for the service, the run fails with
a credentials-stage error (see Run Details below) and the target request is never sent. Schedules
that predate this field were backfilled with `service_id = "dial-oauth"`, then moved to
`"dial-native"`.

For `target_type: chat_completion`, send `properties.create_conversation: true` unless the caller
asked for runs not to be saved — each successful run is then stored as a DIAL conversation titled
after `display_name` and readable in DIAL Chat. See [properties.md](./properties.md).

Creating a schedule for a `service_id` the caller is not currently signed in to is rejected with
**403** before any row is written — see [Sign-In Status](#sign-in-status) below. This is a
separate, earlier check than the run-time OBO failure described above: a **403** here means the
caller must sign in before retrying; a run-time credentials-stage error later means the run itself
was attempted and failed.

## Update Schedule
PUT /schedules/{schedule_id}

Request
```json
{
  "display_name": "",
  "service_id": "dial-native",
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
  "service_id": "dial-native",
  "next_run_time": "datetime|None",
  "trigger": {},
  "properties": {},
  "is_deleted": false,
  "created_by": "",
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

`is_deleted` is read-only. If it is present in the request body it is **ignored** (not rejected),
and PUT never changes it. A soft-deleted schedule cannot be updated at all — PUT returns 409.

The sign-in precondition described under [Insert Schedule](#insert-schedule) applies here too: if
the caller is not signed in to the (possibly changed) `service_id`, the update is rejected with
**403** and the existing schedule is left unchanged.

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
      "service_id": "dial-native",
      "next_run_time": "datetime|None",
      "trigger_type": "cron|date",
      "is_deleted": false,
      "created_by":"user_id",
      "created_at": "datetime",
      "updated_at": "datetime"
    }
  ],
  "next": "/schedules?limit=20&offset=60",
  "previous": "/schedules?limit=20&offset=20"
}
```

Filtered by deletion state via the optional `is_deleted` query parameter (boolean, default
`false`): `false` returns only live schedules, `true` returns only soft-deleted ones. There is no
"all" value, so a page never mixes the two. Hard-deleted schedules are absent under both values.
`count` reflects the filter, and `next`/`previous` echo `is_deleted=true` when it is set. A
non-boolean value returns 422.


## Schedule Details
GET /schedules/{schedule_id}


```json
{
  "id": "str",
  "display_name": "",
  "service_id": "dial-native",
  "next_run_time": "datetime|None",
  "trigger": {},
  "properties": {},
  "is_deleted": false,
  "created_by": "",
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

A soft-deleted schedule is still returned here (200) with `"is_deleted": true` and
`"next_run_time": null`, so its state and its run history stay reachable to its owner.


## Delete Schedule
DELETE /schedules/{schedule_id}

Returns **204** with an empty body. The outcome depends on run history, chosen by the system with
no caller-supplied option:
- **no runs exist** — the scheduler job is unregistered and the schedules row is deleted outright.
  Afterwards the schedule returns 404 and is absent from every list.
- **any run exists** (any status, including `in_progress`) — the scheduler job is unregistered and
  the row is kept with `is_deleted = true`. All run records survive unchanged and stay readable via
  `GET /schedules/{schedule_id}/runs`; `schedule_runs.schedule_id` is `ON DELETE RESTRICT`, so run
  history cannot be lost.

Either way the schedule can never fire again, and there is **no way to restore** a deleted or
soft-deleted schedule.

Errors (the body is a bare JSON string carrying the message, e.g. `"Schedule not found: {id}"`, not
an object):
- **404** — unknown schedule, another user's schedule, or one already hard-deleted.
- **409** — the schedule is already soft-deleted. Re-deleting, updating, pausing, or resuming a
  soft-deleted schedule is always rejected with 409.
- **502** — the scheduler could not unregister the job. Nothing was written to the database; the
  schedule is still live and the request is safe to retry.

The job is unregistered *before* the database write, so a 204 guarantees the schedule cannot fire.
If the database write fails afterwards the caller gets an error and the schedule is left live-looking
but non-firing; retrying the DELETE completes the operation.


## Pause/Resume schedule
POST /schedules/{schedule_id}/pause
POST /schedules/{schedule_id}/resume

Both return 409 for a soft-deleted schedule.


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

## Sign-In Status

GET /sign-in/status?service_id=dial-native

```json
{
  "service_id": "dial-native",
  "signed_in": false,
  "state": "signed_in|not_signed_in|unknown",
  "detail": "human-readable reason, safe to relay verbatim"
}
```

Read-only: it changes no sign-in state and no scheduler state. `service_id` is **required**;
this is the same check the create/update precondition above runs.

`state` is the field to branch on. `signed_in` is a convenience boolean derived from
`state == "signed_in"` — do not use it to distinguish `not_signed_in` from `unknown`. `unknown`
means DIAL could not be reached to determine the answer; it is never reported as `not_signed_in`,
since telling a user to sign in again would not fix an outage.

Since DIAL exposes no dedicated sign-in status endpoint, the answer is determined by attempting
the same on-behalf-of (OBO) credentials lookup the worker performs at run time (see [Insert
Schedule](#insert-schedule) and the credentials-stage run error above): a `403` client error from
DIAL means the user must sign in; a server error or transport failure is the `unknown` case.

This endpoint is exposed over MCP as the `get_sign_in_status` tool, so an assistant can
front-load the sign-in check before collecting every schedule field rather than discovering the
problem only after a create/update is rejected.

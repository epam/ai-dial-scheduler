All endpoints are filtered by created_by, so only creator can read/update schedules.
For Upsert Schedule EP filtering performed only in case of update.

## Upsert Schedule
PUT /schedules/{schedule_id}

```json
{
  "display_name": "", 
  "trigger": {
    "cron": {
      "fields": {
        "year": "",
        "month": "",
        "day": "",
        "week": "",
        "day_of_week": "",
        "hour": "",
        "minute": "",
        "second": ""
      },
      "start_date": "datetime",
      "end_date": "datetime"
    },
    "date": "datetime"
  },
  "properties": {
    "target_type": "chat_completion|rest|sse",
    "method": "POST|PUT|GET|null",
    "url": "",
    "payload": {},
    "extra_headers": [{"tenant_id": "hcscd"}]
  }
}
```

If schedule created, table populated with created_by = user email from /v1/info dial endpoint
TODO: Check if add_job(replace_existing=True) can be used without  reschedule_job()


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
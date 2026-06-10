## schedules
- id: str <- PK, Logical FK to apscheduler_jobs.id (only used for joins, no real FK constraint)
- created_at: datetime
- updated_at: datetime
- display_name: str
- created_by: str
- trigger: json
- kwargs: json


## schedule_runs
- id: uuid
- created_at: datetime
- updated_at: datetime
- end_time: datetime | None
- status: str = "success|error|in_progress|missed",
- result: json
- schedule_id: str <- FK to schedules.id (on delete protect)
- schedule_snapshot: json <- store schedule's display name, trigger and kwargs
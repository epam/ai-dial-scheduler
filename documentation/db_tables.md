## schedules
id: str <- PK, FK apscheduler_jobs.id
display_name: str
created_by: str
created_at: datetime
updated_at: datetime
trigger: json
kwargs: json


## schedule_runs
id: uuid
schedule_id: str <- relation to schedules.id
status: str = "success|error|in_progress|missed", 
start_time: datetime
end_time: datetime | None
result: json
schedule_snapshot: json <- store schedule's display name, trigger and kwargs
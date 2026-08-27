## schedules
- id: str <- PK, Logical FK to apscheduler_jobs.id (only used for joins, no real FK constraint)
- created_at: datetime
- updated_at: datetime
- display_name: str
- created_by: str
- service_id: str <- external service whose on-behalf-of credentials authenticate this
  schedule's execution, e.g. "dial-native"; required on create, backfilled to "dial-oauth"
  for schedules that existed before this column was added, then set to "dial-native" for
  every row when the scheduler adopted DIAL-native offline delegation
- trigger: json
- kwargs: json
- is_deleted: bool <- soft-delete marker, NOT NULL DEFAULT false; set to true by DELETE
  /schedules/{id} only when the schedule has run history (otherwise the row is deleted outright).
  Never cleared — there is no restore. Rows that existed before this column was added were
  backfilled to false


## schedule_runs
- id: uuid
- created_at: datetime
- updated_at: datetime
- end_time: datetime | None
- status: str = "success|error|in_progress|missed",
- result: json
- schedule_id: str <- FK to schedules.id (on delete protect)
- schedule_snapshot: json <- store schedule's display name, trigger and kwargs

# Phase 1 Data Model: Schedule Management Endpoints

This feature adds **no new database tables**. It consumes the existing `schedules` (app-owned)
and `apscheduler_jobs` (engine-owned) tables from feature 006. The "models" below are the
**Pydantic request/response schemas** and the **enums** that define the API contract and
validation, plus how they map to the persisted columns.

## Persistence mapping (existing tables)

| API field | Persisted as | Notes |
|-----------|--------------|-------|
| `id` | `schedules.id` (`VARCHAR(191)`) | `str(uuid4())`, also used as APScheduler job id |
| `display_name` | `schedules.display_name` | non-empty |
| `created_by` | `schedules.created_by` | `MOCK_USER_ID` this iteration |
| `trigger` | `schedules.trigger` (`JSONB`) | serialized `ScheduleTrigger` |
| `properties` | `schedules.kwargs` (`JSONB`) | serialized `ScheduleProperties` (API `properties` == `schedules.kwargs`) |
| `created_at` / `updated_at` | `schedules.*` (`TIMESTAMPTZ`) | server-managed (`BaseSQLModel`) |
| `next_run_time` | `apscheduler_jobs.next_run_time` (`Double`) | epoch seconds (UTC) → aware `datetime`; read-only |

The APScheduler job `kwargs` written via `add_job` = `properties.model_dump()` **plus**
`schedule_id` injected at registration time.

## Enums (`src/lib/db/enums.py`)

```text
TargetType(StrEnum):  CHAT_COMPLETION = "chat_completion"; REST = "rest"; SSE = "sse"
HttpMethod(StrEnum):  POST = "POST"; PUT = "PUT"; GET = "GET"
TriggerType(StrEnum): CRON = "cron"; DATE = "date"
```

## Schema: `CronFields`

All fields optional, `str | int | None` (default `None`). Mirrors APScheduler `CronTrigger`
field names.

| Field | Type | Notes |
|-------|------|-------|
| `year`, `month`, `day`, `week`, `day_of_week`, `hour`, `minute`, `second` | `str \| int \| None` | passed through to `CronTrigger(**fields)` |

## Schema: `CronTriggerSpec`

| Field | Type | Notes |
|-------|------|-------|
| `fields` | `CronFields` | required object (may have all-None members) |
| `start_date` | `datetime \| None` | optional |
| `end_date` | `datetime \| None` | optional |

## Schema: `ScheduleTrigger`

| Field | Type | Notes |
|-------|------|-------|
| `cron` | `CronTriggerSpec \| None` | optional |
| `date` | `datetime \| None` | optional |

**Validation** (`model_validator(mode="after")`):
- Exactly one of `cron` / `date` MUST be set. Zero set → error "exactly one trigger required".
  Both set → error "only one trigger allowed". (FR-021)

**Derived**: `trigger_type` → `TriggerType.CRON` if `cron` else `TriggerType.DATE` (used in list rows).

## Schema: `ScheduleProperties`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `target_type` | `TargetType` | — | required; drives branching (FR-024) |
| `method` | `HttpMethod \| None` | `None` | not used for `chat_completion` |
| `url` | `str` | — | required, non-empty |
| `payload` | `dict[str, Any]` | `{}` | for `chat_completion`, `stream` read with default `False` |
| `extra_headers` | `dict[str, str]` | `{}` | overrides injected headers of the same name |
| `api_version` | `str \| None` | `None` | used only for `chat_completion` |

**Validation** (`model_validator(mode="after")`, FR-025/FR-026):

| `target_type` | `method` | `url` | `api_version` | `payload` |
|---------------|----------|-------|---------------|-----------|
| `chat_completion` | MUST be absent (rejected with `ValidationError` if set) | MUST be a valid OpenAI-compatible deployment URL (well-formed http(s) URL) | applicable (optional) | `stream` extracted if present, else `False` |
| `rest` | REQUIRED (`POST`/`PUT`/`GET`) | valid http(s) URL | MUST be absent (rejected with `ValidationError` if set) | arbitrary JSON |
| `sse` | REQUIRED (`POST`/`PUT`/`GET`) | valid http(s) URL | MUST be absent (rejected with `ValidationError` if set) | arbitrary JSON |

Failures raise Pydantic `ValidationError` → HTTP 422 before any persistence.

## Schema: `ScheduleRequest` (Create / Update share one model)

| Field | Type | Notes |
|-------|------|-------|
| `display_name` | `str` | non-empty |
| `trigger` | `ScheduleTrigger` | validated (cron xor date) |
| `properties` | `ScheduleProperties` | validated per target type |

Used for both `POST /schedules/` and `PUT /schedules/{schedule_id}` (FR-001/FR-009).

## Schema: `ScheduleResponse` (Create / Update / Details)

| Field | Type | Source |
|-------|------|--------|
| `id` | `str` | `schedules.id` |
| `display_name` | `str` | `schedules.display_name` |
| `next_run_time` | `datetime \| None` | `apscheduler_jobs.next_run_time` (converted) |
| `trigger` | `dict` (serialized `ScheduleTrigger`) | `schedules.trigger` |
| `properties` | `dict` (serialized `ScheduleProperties`) | `schedules.kwargs` |
| `created_by` | `str` | `schedules.created_by` |
| `created_at` | `datetime` | `schedules.created_at` |
| `updated_at` | `datetime` | `schedules.updated_at` |

## Schema: `ScheduleListItem`

| Field | Type | Source |
|-------|------|--------|
| `id` | `str` | `schedules.id` |
| `next_run_time` | `datetime \| None` | join to `apscheduler_jobs` |
| `trigger_type` | `TriggerType` (`cron`/`date`) | derived from `schedules.trigger` |
| `created_by` | `str` | `schedules.created_by` |
| `created_at` | `datetime` | `schedules.created_at` |
| `updated_at` | `datetime` | `schedules.updated_at` |

## Schema: `PaginatedSchedules`

| Field | Type | Notes |
|-------|------|-------|
| `count` | `int` | total owned schedules (FR-014) |
| `limit` | `int` | echo of request limit (default 20) |
| `offset` | `int` | echo of request offset (default 0) |
| `results` | `list[ScheduleListItem]` | current page |
| `next` | `str \| None` | `/schedules?limit=&offset=` for next page, else `None` (FR-016) |
| `previous` | `str \| None` | previous-page link, else `None` (FR-016) |

## Repository surface (`ScheduleRepository`, async, ORM/Core only)

| Method | Returns | Purpose |
|--------|---------|---------|
| `create(schedule: Schedule)` | `Schedule` | insert + commit |
| `update(schedule_id, created_by, *, display_name, trigger, kwargs)` | `Schedule \| None` | update owned row + commit; `None` if not found/owned |
| `get(schedule_id, created_by)` | `(Schedule, next_run_time) \| None` | owned row + LEFT JOIN next_run_time |
| `list(created_by, limit, offset)` | `(count, list[(Schedule, next_run_time)])` | paginated, scoped, LEFT JOIN |
| `delete(schedule_id)` | `None` | compensating delete on create failure |

## Service surface (`ScheduleService`)

| Method | Input | Output | Behavior |
|--------|-------|--------|----------|
| `create(request, created_by)` | `ScheduleRequest`, `str` | `ScheduleResponse` | gen id, persist, `add_job` (to_thread), saga rollback on failure, read `next_run_time` |
| `update(schedule_id, request, created_by)` | `str`, `ScheduleRequest`, `str` | `ScheduleResponse` | load+capture, update, `add_job(replace_existing=True)`, saga restore on failure |
| `list(created_by, limit, offset)` | `str`, `int`, `int` | `PaginatedSchedules` | scoped page + nav links |
| `get(schedule_id, created_by)` | `str`, `str` | `ScheduleResponse` | owned details or `ScheduleNotFoundError` |

## Exceptions (`src/lib/exceptions/__init__.py`)

| Exception | Maps to | When |
|-----------|---------|------|
| `ScheduleNotFoundError` | HTTP 404 | get/update on missing or non-owned id (FR-018) |
| `JobRegistrationError` | HTTP 500/502 | `add_job` failed after row persisted (post-rollback) (FR-007/FR-012) |

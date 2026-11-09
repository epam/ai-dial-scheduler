# Data Model: Per-Schedule Timezone

## Table `schedules`: new column

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| `timezone` | `VARCHAR(64)` | NOT NULL | `'UTC'` (server default, kept permanently) | IANA identifier, stored exactly as submitted. Not indexed. |

Migration `migrations/versions/20261104_1200_<rev>_add_timezone_to_schedules.py`, which revises `b7c5e6f8a9d0`:

- `upgrade`: `op.add_column("schedules", sa.Column("timezone", sa.String(64), nullable=False, server_default=sa.text("'UTC'")))`. This backfills every existing row in the same statement.
- `downgrade`: `op.drop_column("schedules", "timezone")`.

The migration uses literals and does not import from `src`, so it stays frozen even if constants change later.

## ORM model `Schedule`

```python
timezone: str = Field(
    default=DEFAULT_SCHEDULE_TIMEZONE,
    sa_column=Column("timezone", String(64), nullable=False, server_default=text("'UTC'")),
)
```

Add `timezone` to the class docstring's Attributes.

## Constants (`src/lib/constants.py`)

| Name | Value |
|------|-------|
| `DEFAULT_SCHEDULE_TIMEZONE` | `"UTC"` |

## Schemas (`src/lib/schemas/schedule.py`)

| Model | Field | Type | Notes |
|-------|-------|------|-------|
| `ScheduleRequest` | `timezone` | `str`, default `"UTC"` | A `mode="before"` validator turns `None` into `"UTC"`. An after-validator requires membership in the cached `zoneinfo.available_timezones()`. `Field(description=...)` feeds OpenAPI and MCP. |
| `ScheduleResponse` | `timezone` | `str`, required | Stored value. |
| `ScheduleListItem` | `timezone` | `str`, required | Stored value. |
| `CronTriggerSpec.trigger_kwargs` | parameter `timezone: tzinfo = UTC` | | Replaces the hard-coded `UTC`. |

**Validation outcomes**

| Input | Result |
|-------|--------|
| omitted, `null` | `"UTC"` |
| `"Europe/Berlin"`, `"Europe/Kiev"`, `"Europe/Kyiv"`, `"UTC"`, `"Etc/UTC"` | accepted as is |
| `"europe/berlin"`, `"Mars/Olympus"`, `"+02:00"`, `""` | 422 at `body.timezone` |

## Runtime mapping (not persisted in `schedules`)

| Stored trigger | APScheduler trigger, all with `timezone=ZoneInfo(schedule.timezone)` |
|----------------|------|
| `cron` | `WallClockCronTrigger` |
| `crons` with 1 entry | `WallClockCronTrigger` |
| `crons` with 2+ entries | `InstantOrTrigger([EntryCronTrigger, ...])`, where `EntryCronTrigger` subclasses `WallClockCronTrigger` and `InstantOrTrigger` orders children by timestamp |
| `date` | `DateTrigger` |

Naive datetimes in the trigger are localised in the schedule's zone. Aware ones keep their instant.

## Stored-row compatibility

| Row | `timezone` | Pickled APScheduler trigger | Behaviour |
|-----|-----------|------------------|-----------|
| Before this feature | `'UTC'` (backfilled) | plain `CronTrigger`/`OrTrigger`, UTC | Unchanged. UTC has no DST, so no re-registration is needed. |
| After, default | `'UTC'` | `WallClockCronTrigger`, UTC | Same instants as before. |
| After, zone set | for example `'Europe/Berlin'` | `WallClockCronTrigger` in that zone | Fires at local wall-clock times. |

## State transitions

- **Create** stores the timezone with the row, then registers the job in that zone. If registration fails, the row is rolled back as today.
- **Update** replaces the timezone together with the rest of the body. If re-registration fails, the prior timezone is restored together with the other prior values.
- **Pause and resume**: the stored trigger keeps its zone, so resume computes `next_run_time` in it.
- **Delete**: unchanged.

# Data Model: Multiple Cron Triggers per Schedule

No database schema change. `schedules.trigger` (JSONB) stores the serialized `ScheduleTrigger`; no Alembic migration is required.

## ScheduleTrigger (request/stored shape)

| Field | Type | Notes |
|-------|------|-------|
| `cron` | `CronTriggerSpec \| null` | Existing. Single cron. |
| `crons` | `list[CronTriggerSpec] \| null` | New. 1 to 20 entries. |
| `date` | `datetime \| null` | Existing. One-off run. |

**Validation**
- Exactly one of `cron`, `crons`, `date` is set. Setting more than one, or none, is rejected.
- `crons` has `min_length=1` and `max_length=20` (constant in `src/lib/constants.py`).
- Each entry is validated as a `CronTriggerSpec`, so errors locate the entry (`trigger.crons.1.fields...`).
- Duplicate entries are allowed.

**Derived**
- `trigger_type` is `cron` when `cron` or `crons` is set, otherwise `date`.

## CronTriggerSpec (unchanged)

`fields: CronFields`, `start_date: datetime | null`, `end_date: datetime | null`.

## Stored-row compatibility

| Row written | `cron` | `crons` key | Result |
|-------------|--------|-------------|--------|
| Before this feature | object | absent | Validates; `crons` defaults to `null`. |
| After, single cron | object | `null` | Same behaviour. |
| After, list | `null` | array | Fires on the union of entries. |

Clients must treat an absent `crons` key as `null`.

## Runtime mapping (not persisted)

| Stored trigger | APScheduler trigger |
|----------------|---------------------|
| `cron` | `CronTrigger` |
| `crons` with 1 entry | `CronTrigger` |
| `crons` with 2+ entries | `OrTrigger([CronTrigger, ...])` |
| `date` | `DateTrigger` |

All triggers are anchored to UTC.

## State transitions

Update replaces the whole trigger. Any of `cron`, `crons`, `date` may be replaced by any other; entries are never merged. The existing compensating restore of the prior trigger on re-registration failure applies unchanged.

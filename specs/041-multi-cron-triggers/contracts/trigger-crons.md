# Contract: `trigger.crons`

Applies to `POST /v1/schedules` and `PUT /v1/schedules/{id}` request bodies, and to the `trigger` object in all schedule and run responses. The change is additive; existing requests are unaffected, so the contract version does not change major.

## Request

`trigger` carries exactly one of `cron`, `crons`, `date`.

```json
{
  "display_name": "Weekly report",
  "service_id": "dial-native",
  "trigger": {
    "crons": [
      { "fields": { "day_of_week": "mon", "hour": 8, "minute": 0 } },
      { "fields": { "day_of_week": "fri", "hour": 10, "minute": 0 },
        "start_date": "2026-11-01T00:00:00Z", "end_date": null }
    ]
  },
  "properties": { "...": "unchanged" }
}
```

| Field | Type | Constraint |
|-------|------|------------|
| `trigger.crons` | array of cron spec | 1 to 20 entries. Same entry shape as `trigger.cron`. |

## Responses

- `trigger` is returned as submitted. A list schedule returns `"crons": [...]` and `"cron": null`.
- Schedules written before this feature may lack the `crons` key; treat as `null`.
- `trigger_type` (list endpoint) is `"cron"` for both `cron` and `crons`.
- `next_run_time` is the earliest upcoming fire time across all entries, or `null` if all entries have expired or the schedule is paused.

## Errors (HTTP 422)

| Case | Error location |
|------|----------------|
| `crons: []` | `trigger.crons` |
| More than 20 entries | `trigger.crons` |
| Invalid field in entry `i` | `trigger.crons.<i>...` |
| More than one of `cron`/`crons`/`date`, or none | `trigger` |

## Documentation deliverables (FR-015)

- `documentation/api_contract.md`: request example, response notes, error table.
- OpenAPI examples in `src/api/v1/endpoints/schedules.py`: add a multi-cron example.
- `trigger` field description on `ScheduleTrigger`, which the MCP tool input schema inherits.
- `documentation/requirements.md` if it describes trigger rules.
- `CHANGELOG.md` under `[Unreleased] > Added`.

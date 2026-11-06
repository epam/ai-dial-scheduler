# Contract: `timezone` on schedules

Applies to the request bodies of `POST /v1/schedules` and `PUT /v1/schedules/{id}`, and to the bodies of the create, update, detail, pause and resume responses and of list items. The change is additive, so the contract's major version does not change.

## Request

```json
{
  "display_name": "Daily report",
  "service_id": "dial-native",
  "timezone": "Europe/Berlin",
  "trigger": {
    "cron": { "fields": { "day_of_week": "mon-fri", "hour": 9, "minute": 0 } }
  },
  "properties": { "...": "unchanged" }
}
```

| Field | Type | Required | Default | Constraint |
|-------|------|----------|---------|------------|
| `timezone` | string or `null` | no | `"UTC"` | An IANA Time Zone Database identifier, case-sensitive. `null` means `"UTC"`. |

- Every value returned by `Intl.supportedValuesOf('timeZone')` in current browsers is accepted, including legacy names such as `Europe/Kiev` and `Asia/Calcutta`. `UTC` is accepted as well. The reference list is the test fixture [tests/helpers/browser_timezones.json](../../../tests/helpers/browser_timezones.json); validation itself uses `zoneinfo.available_timezones()`, not this list.
- Current IANA names (`Europe/Kyiv`) and other identifiers in the IANA database are accepted too. Region/City names are recommended. In `Etc/GMT±N` the sign is inverted: `Etc/GMT+2` is UTC−2.
- `PUT` is a full replacement. Omitting `timezone` resets it to `UTC`, so clients must send it on every update.

## Semantics

- Cron fields in `cron` and in every `crons` entry are local wall-clock times in `timezone`.
- `start_date`, `end_date` and `date` without an offset are local times in `timezone`. With an offset or `Z` they are absolute instants.
- **Spring forward**: a local time that does not exist fires once, shifted forward by the length of the gap. In `Europe/Berlin`, 02:30 becomes 03:30.
- **Fall back**: a specific local time that occurs twice fires once, at its first occurrence. Entries whose `hour` matches all 24 hours (`*`, `*/1`, `0-23`) fire on every real hour, including both occurrences of the repeated hour.

## Responses

```json
{
  "id": "…",
  "timezone": "Europe/Berlin",
  "next_run_time": "2026-11-05T08:00:00Z",
  "trigger": { "cron": { "fields": { "day_of_week": "mon-fri", "hour": 9, "minute": 0 }, "start_date": null, "end_date": null }, "crons": null, "date": null },
  "...": "unchanged"
}
```

- `timezone` is always present and is returned exactly as submitted, without renaming. Schedules created before this feature return `"UTC"`.
- `next_run_time` and every other timestamp stay absolute UTC instants. Converting them to local time for display is the client's job.
- List items (`GET /v1/schedules`) carry `timezone` as well.

## Errors (HTTP 422)

| Case | Location | Message |
|------|----------|---------|
| Unknown name (`Mars/Olympus`) | `body.timezone` | `unknown timezone 'Mars/Olympus'; expected an IANA identifier such as 'Europe/Berlin'` |
| Wrong case (`europe/berlin`) | `body.timezone` | same |
| UTC offset (`+02:00`) | `body.timezone` | same |
| Empty string | `body.timezone` | same |
| Not a string (`120`) | `body.timezone` | Pydantic type error |

A rejected request creates, changes and reschedules nothing.

## Documentation deliverables (FR-015)

- `documentation/api_contract.md`: the field in the create/update request, responses and list item, a "Timezone" paragraph with the DST rules, and the error row. Replace "All times are UTC".
- `documentation/db_tables.md`: the `timezone` column.
- OpenAPI: a `with_timezone` example in `src/api/v1/endpoints/schedules.py`, `Field(description=...)` on `ScheduleRequest.timezone`, and an updated `ScheduleTrigger.crons` description. MCP tools inherit all three.
- `tests/e2e/1.5-Create-schedule-with-timezone.http`.
- `CHANGELOG.md` under `[Unreleased]`: `timezone` under Added, and the `tzdata` dependency under Changed.

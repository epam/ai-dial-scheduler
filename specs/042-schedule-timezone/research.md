# Research: Per-Schedule Timezone

All findings were measured on 2026-11-04 with APScheduler 3.11.2 and Python 3.14.2, on macOS and in `python:3.14-alpine` (tzdata 2026e). The reference browser list comes from Node 25.9.0 (ICU 78.3, tz 2026a) and is saved as the test fixture [tests/helpers/browser_timezones.json](../../tests/helpers/browser_timezones.json).

## R1 — Where the field sits in the API

- **Decision**: A top-level `timezone` field on `ScheduleRequest`, `ScheduleResponse` and `ScheduleListItem`, beside `trigger`. This is the user's choice.
- **Rationale**: The spec makes the timezone a property of the schedule that applies to the whole trigger (`cron`, every `crons` entry and `date`). A top-level field matches that, and it is the simplest shape for the frontend to bind to a dropdown.
- **Alternatives considered**: `trigger.timezone` inside the JSONB trigger. It needs no migration, but it mixes a schedule property into the trigger shape, and the list endpoint would have to parse the JSON to expose it.

## R2 — Storage

- **Decision**: A new column `schedules.timezone VARCHAR(64) NOT NULL DEFAULT 'UTC'`, added by one Alembic migration that revises `b7c5e6f8a9d0`. The server default is kept permanently.
- **Rationale**: `ADD COLUMN ... NOT NULL DEFAULT 'UTC'` backfills every existing row in the same statement without a table rewrite (PostgreSQL 11+), which meets FR-010 with no separate data step. This is the same pattern as `is_deleted` (`20260810_1200_e5f3c4d6a7b8`). The longest IANA identifier is 32 characters (`America/Argentina/ComodRivadavia`), so 64 leaves headroom. No index is added because nothing filters or sorts by timezone (out of scope).
- **Alternatives considered**: a nullable column where `NULL` means UTC. Rejected because every reader would have to apply the default, and responses must always carry a value (FR-003, FR-010).

## R3 — Accepted values and validation

- **Decision**: Accept a value only if it is in `zoneinfo.available_timezones()`. The set is computed once and cached with `functools.cache`. `None` becomes `"UTC"` in a `mode="before"` validator. Everything else fails with a validation error at `body.timezone`. Add `tzdata` as a direct dependency (`tzdata>=2026.2`; it is already in `uv.lock`, but only for `win32`).
- **Rationale**:
  - All 418 browser identifiers and `UTC` are in the set on both platforms, which covers FR-006.
  - Membership in a set is case-sensitive, so `europe/berlin` is rejected everywhere. Using `ZoneInfo(name)` alone is not enough. It accepts `europe/kyiv` on macOS, where the file system ignores case, and rejects it in Alpine. That would break FR-007.
  - UTC offsets (`+02:00`), empty strings and unknown names are not in the set.
  - With `tzdata` installed, `zoneinfo` falls back to the package when the OS has no zone files. The worker and the API then resolve the same names regardless of the base image. Today they depend on the Alpine `tzdata` apk being present.
- **Known looseness**: `available_timezones()` also returns `Factory` (an IANA placeholder zone at UTC with the abbreviation `-00`), and it unions zone files found on the host's `TZPATH` with the `tzdata` package. Both are harmless for scheduling (a host-only name would also resolve in the same container), so no deny-list is kept.
- **Accepted beyond the browser set**: other IANA identifiers that the database still ships, such as `Europe/Kyiv`, `Etc/UTC`, `US/Eastern`, `EST`, `CET` and `Etc/GMT+2` (598 in total). They are valid IANA names and APScheduler handles them. Rejecting them would need a hand-kept deny-list (YAGNI). The documentation recommends region/city names and notes that the sign in `Etc/GMT±N` is inverted.
- **Alternatives considered**: a hard-coded enum of the 418 browser names. Rejected because it rejects current names (`Europe/Kyiv`) that API and MCP clients will send, and it goes stale with each ICU release.

## R4 — Evaluating triggers in the schedule's timezone

- **Decision**: Change `build_trigger(trigger)` to `build_trigger(trigger, timezone)`, where `timezone` is the IANA name. It resolves `ZoneInfo(timezone)` once and passes it to every APScheduler trigger it builds. `CronTriggerSpec.trigger_kwargs()` gets a `timezone: tzinfo` parameter that defaults to `UTC`, so the request-time field validator keeps working without knowing the schedule's zone.
- **Rationale**: APScheduler evaluates cron fields in `CronTrigger.timezone`. It also localises naive `start_date`, `end_date` and `DateTrigger.run_date` in that zone and leaves aware values unchanged. This was verified: a naive `2027-01-01T00:00:00` with `Europe/Berlin` becomes `00:00+01:00`. That gives FR-004 and FR-005 with no conversion code of our own. Non-whole-hour zones work: `Asia/Kolkata` daily 09:00 gives `09:00+05:30`.
- **Unchanged for UTC**: when `timezone == "UTC"`, the triggers that are built are equivalent to today's.

## R5 — Daylight-saving fall back: a repeated local time fires twice

- **Finding**: A plain `CronTrigger(hour=2, minute=30, timezone=Europe/Berlin)` fires on 2027-10-31 at both `02:30+02:00` and `02:30+01:00`. That violates FR-013.
- **Superseded by R5a** (the original decision is kept below for history). **Original decision**: Add `WallClockCronTrigger(CronTrigger)` in `src/lib/services/trigger_factory.py`. In `get_next_fire_time`, when the candidate is the second occurrence of a repeated wall time (`candidate.fold == 1`, and the two folds have different UTC offsets) and the hour field is not `*`, it searches again from that candidate (`super().get_next_fire_time(candidate, candidate)`). It loops until the candidate is acceptable or `None`. `EntryCronTrigger` now subclasses `WallClockCronTrigger` and keeps its own `start_date` fix. `build_trigger` uses `WallClockCronTrigger` for a single `cron` and for a one-entry `crons`, so every cron schedule follows the same rule.
- **Wildcard detection** (superseded by R5a, which probes all 24 hours so `*/1` and `0-23` also count): use `str(hour_field) == "*"`, not `hour_field.is_default`. APScheduler marks an unspecified hour as default even when it fills it with `0`. `CronTrigger(day=1)` has `is_default=True` but the hour expression is `0`, so `is_default` would wrongly treat it as "every hour".
- **Verified with a prototype**:

  | Trigger (`Europe/Berlin`, 2027-10-31) | Fires |
  |---|---|
  | daily 02:30 | `02:30+02:00` once, then the next day. One duplicate removed. |
  | hourly (`minute=0`) | `01:00+02`, `02:00+02`, `02:00+01`, `03:00+01`: every real hour |
  | `hour="1-3", minute="*/30"` | the first `02:00` and `02:30` only, then `03:00+01`. This matches classic cron. |
  | `OrTrigger` of daily 02:30 and Monday 08:00 | one 02:30, then the following entries |

- **Pickling**: the job store pickles the class path. `WallClockCronTrigger` and `EntryCronTrigger` must keep their names and module (`src.lib.services.trigger_factory`). Changing the base class of `EntryCronTrigger` does not change its pickled state (`CronTrigger.__getstate__`, version 2), so stored multi-cron jobs keep loading.
- **Alternatives considered**: do nothing and document the duplicate (rejected by FR-013 and SC-004); upgrade to APScheduler 4 (still pre-release, and a major migration).

## R5a — Revision after code review: APScheduler's cron stepping is not DST-safe

- **Findings** (code review C1/I1 plus a property check written in response):
  - `OrTrigger` picks `min()` of child fire times. Same-zone datetimes compare by wall-clock fields, ignoring offset and `fold`, so multi-cron schedules dropped slots on both change days (for example hourly plus 02:30 on 2027-10-31: the 02:30 entry never fired).
  - A child handed a sibling's fire time reasons in wall-clock fields, so a slot shifted out of a forward gap (02:30 firing at 03:30) was skipped after a sibling's 03:15.
  - `CronTrigger._increment_field_value` adds wall-clock differences to real timestamps. Across a forward gap this overshoots by the lost hour: a daily 00:59 `Europe/Berlin` entry jumps from 2027-03-28 straight to 2027-03-30. This is a library bug that UTC-only scheduling never exposed.
  - `str(hour) == "*"` missed equivalent every-hour expressions (`*/1`, `0-23`).
- **Decision**: `WallClockCronTrigger` no longer lets APScheduler step in the schedule's zone. It steps a UTC copy of the same fields (no clock changes exist there) through naive wall-clock times and maps each match to instants itself:
  - a missing local time maps to its shifted instant (`fold=0` offset);
  - a repeated local time maps to its first instant, plus the second one only if the hour field matches all 24 hours (probed with `get_next_value` for each hour);
  - the result is the earliest instant at or after the lower bound (`min(now, previous + 1 µs)`, raised to `previous + 1 µs` if needed, and to `start_date` on every call), compared by timestamp.

  Near a change, wall-clock order and instant order disagree by at most the change's size, so the scan starts and continues that much further; away from changes it stops at the first match. `InstantOrTrigger(OrTrigger)` picks the earliest child by timestamp. `EntryCronTrigger` keeps its name only because it is pickled; its start-date workaround is now built into the base class.
- **Verification**: a property check compared the triggers with an oracle that enumerates every local minute around both changes and applies the spec rules directly: 7,400 random single and multi-entry schedules over 9 zones (Berlin, New York, Lord Howe with 30-minute changes, Azores and Beirut at midnight, Santiago at 24:00, Sydney, Chatham with 45-minute offsets, Tokyo without DST), with 0 mismatches. Regression tests pin each failure above, including one that asserts the plain `CronTrigger` bug still exists, so a library fix is noticed.
- **Cost**: 10–25 µs per call away from changes. Within an hour or so of a change the scan covers up to two hours of wall-clock matches: about 0.4 ms for a 5-minute schedule and about 48 ms for an every-second schedule.

## R6 — Daylight-saving spring forward: a local time that does not exist

- **Finding**: daily 02:30 `Europe/Berlin` on 2027-03-28 fires at 01:30 UTC. That is 03:30 local, shifted forward by the length of the gap, and it fires once. This already satisfies FR-012. APScheduler labels the instant `02:30+01:00`, but the stored value is an epoch, so `next_run_time` is correct.
- **Decision**: No code. A regression test pins the behaviour.

## R7 — Job store and worker

- **Decision**: No worker change. `CronTrigger.__getstate__` stores the `ZoneInfo`, which pickles by key and is resolved again with `ZoneInfo(key)` when the worker loads the job. This was verified with `Europe/Kiev`. Both services run from the same image and therefore have the same `tzdata` (R3).
- **Existing jobs**: rows pickled before this feature hold plain UTC `CronTrigger`s. UTC has no daylight saving, so their behaviour is already correct and they are not re-registered (FR-010). They switch to the new classes the next time they are updated.

## R8 — Update semantics and compensation

- **Decision**: `PUT` stays a full replacement. An omitted or `null` `timezone` stores `UTC` (spec assumption). `ScheduleService.update` adds `prior_timezone` to the values it restores if re-registration fails. `ScheduleRepository.update` gets a required keyword `timezone: str`, so a caller cannot reset the timezone by accident.
- **Pause and resume**: no change. Resume asks APScheduler for the next fire time from the stored trigger, which already carries the zone (FR-011).

## R9 — Response shape

- **Decision**: Responses return `schedule.timezone`, the stored string, unchanged. `next_run_time`, run times and audit timestamps stay UTC instants (FR-014). The `trigger` dict is still returned as submitted. Naive datetimes inside it stay naive in the response and mean local time in `timezone`.

## R10 — Contract, MCP and documentation

- **Decision**: An additive OpenAPI change, so no major bump (constitution IV). The MCP server builds its tools from `app.openapi()`, so the `Field(description=...)` on `ScheduleRequest.timezone` becomes the tool documentation. Replace "All times are UTC" in the `ScheduleTrigger.crons` description with "Times are local to the schedule's `timezone`".

## R11 — Observability

- **Decision**: Add `timezone` to the existing "Schedule job registered" and "Schedule job re-registered" log entries through `_trigger_log_fields`. A timezone name is not sensitive.

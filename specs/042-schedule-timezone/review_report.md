# Review Report: Per-Schedule Timezone

- **spec-dir**: `specs/042-schedule-timezone`
- **Date**: 2026-11-04
- **Mode**: Verification (run 2). Run 1 was a fresh review on the same date.
- **Artifacts consulted**: `spec.md`, `plan.md`, `research.md` (including the new section R5a), `data-model.md`, `tasks.md`, `contracts/schedule-timezone.md`, `contracts/browser-timezones.json`
- **Change set**: the uncommitted diff on `042-schedule-timezone` vs `HEAD`, plus the untracked migration, migration test, `tests/helpers/browser_timezones.json` and the e2e `.http` file. Requested scope for this run: verify every recorded issue, and review the rewritten `src/lib/services/trigger_factory.py` from scratch.

**Verification run during this review**

- `uv run ruff check src tests migrations`: pass. `uv run ruff format --check src tests migrations`: pass. `uv run mypy src`: pass.
- `uv run pytest tests/unit`: 1587 passed (1572 in run 1).
- `TEST_DB_URL=... uv run pytest tests/integration`: 240 passed.
- **Independent DST oracle** (written for this review, separate from the one in R5a). It builds random single-entry and multi-entry schedules: random hour sets (including all 24 hours and ranges around the change), random minute sets and step sizes, and optional day-of-week limits. The oracle walks every local minute from 30 h before to 30 h after each 2027 offset change and applies the spec rules directly. A gap time maps to its shifted instant. A repeated time maps to its first instant, plus the second one only when the hour set is all 24 hours. Two checks were run against that oracle. First, the full chain of `get_next_fire_time(prev, prev)` calls over the window. Second, `get_next_fire_time(None, now)` for 10 random `now` values within ±4 h of each change, which exercises the scan-window and horizon logic. Zones covered: Berlin, New York, Lord Howe (30-minute change), Santiago, Troll (2-hour change, exactly `_MAX_CLOCK_CHANGE`), Havana and Azores (midnight changes), Chatham, Beirut, Asuncion, and Casablanca (Ramadan reverse changes). Result: **4,675 checks, 0 mismatches**. The slowest single call was 3.2 ms.
- Edge-case probes: a `start_date` inside the gap, an `end_date` inside the repeated hour, `jitter`, and `previous_fire_time > now`. Results are quoted in M7 and the Fresh review section.
- Timing near a change (2027-03-28 00:30Z, Berlin), per `get_next_fire_time` call. Away from a change every case takes about 0.011 to 0.019 ms. Near the change: `second="*"` 32 ms, `second="*/10"` 4.3 ms, `minute="*"` 0.78 ms, `minute="*/5"` 0.21 ms (see M8).

## Summary

**Approved with minor suggestions.** C1 and I1 are fixed. The new trigger design steps a UTC copy of the cron fields over naive wall-clock time, maps each match to instants, and picks the earliest child by timestamp. It is correct for every case I could construct, including zones and change sizes that the R5a property check did not cover. The only item still open from run 1 is the partial I2: two of the requested multi-cron DST regression tests are still missing. Four new minor items concern clarity, a known performance cost and stale design text. None of them blocks merging.

## Critical Issues

### C1: `OrTrigger` orders children by wall-clock label, not by instant, so multi-cron schedules miss runs on DST days
- **Status**: Resolved
- **File**: `src/lib/services/trigger_factory.py:123-143` (`InstantOrTrigger`), `:187-190` (`_build_crons_trigger`)
- **Original problem**: stock `OrTrigger.get_next_fire_time` used `min()` on same-zone aware datetimes, which compares wall-clock fields and ignores offset and `fold`. Runs were lost on both change days (four reproduced scenarios in `Europe/Berlin`). This violated FR-012, FR-013, SC-004 and US1 scenario 3.
- **Resolution**: `InstantOrTrigger(OrTrigger)` now picks `min(pending, key=datetime.timestamp)`, has `__slots__ = ()`, carries the "keep name and module stable" pickle note, and is what `_build_crons_trigger` returns for two or more entries. A second, related defect was found while fixing C1. A child handed a sibling's previous fire time reasoned in wall-clock fields and skipped a gap-shifted slot. `WallClockCronTrigger` now compares everything by timestamp, which fixes that as well. All four run-1 scenarios now give the correct sequence. Three are pinned by tests (`TestBuildTriggerCronsDaylightSaving`), and the oracle confirms all four plus thousands of random variants. The pickle round trip is tested (`test_instant_ordered_or_trigger_survives_pickle_round_trip`). Jobs already pickled with stock `OrTrigger` are UTC-only, so they are unaffected.

## Improvements

### I1: The "every hour" check matches only the literal `*`, so `hour="*/1"` and `hour="0-23"` lose a real hour on fall-back day
- **Status**: Resolved
- **File**: `src/lib/services/trigger_factory.py:97-100` (`_fires_every_hour`)
- **Resolution**: the hour field is now probed with `get_next_value` for each of the 24 hours, which implements the run-1 suggestion. `*`, `*/1` and `0-23` fire through both passes, and `*/2`, `0-22` and `2` do not (`TestEveryHourDetection`, parametrised). The default-filled hour `0` (for example `day_of_week="sun"`) is still correctly treated as a fixed time (`test_default_hour_zero_is_not_treated_as_every_hour`). `api_contract.md`, `contracts/schedule-timezone.md` and CHANGELOG now say "matches all 24 hours (`*`, `*/1`, `0-23`)".

### I2: The DST behaviour of multi-cron schedules is untested beyond two fixed entries
- **Status**: Open (mostly addressed)
- **File**: `tests/unit/lib/services/test_trigger_factory.py:337-385`
- **Progress**: these are now covered:
  - spring forward with two fixed entries (`test_spring_entry_after_gap_is_not_lost`);
  - fall back with hourly plus fixed entries (`test_fall_fixed_entry_still_fires_beside_hourly_entry`);
  - fall back with two hourly entries (`test_fall_two_hourly_entries_keep_every_real_slot`);
  - an entry `start_date` inside the repeated hour;
  - a gap-shifted slot coinciding with a real slot;
  - the APScheduler stepping regression.
- **Still missing**:
  1. The fourth C1 scenario, **spring forward, hourly plus fixed** (`minute=0` and `hour=2, minute=30` from 2027-03-27 23:30Z). Expected: `00:00Z`, `01:00Z` (03:00 CEST, the hourly run that used to be dropped), `01:30Z` (02:30 shifted), `02:00Z`.
  2. An **`America/New_York` multi-cron case on a DST change day** for US1 scenario 3. `test_crons_entries_fire_at_local_times` uses New York, but on ordinary weeks only.

  My oracle shows both pass today. They are cheap regression pins built from the existing `_utc_fire_times` helper.

## Minor Suggestions

- **M1** (Resolved): `src/lib/models/schedule.py:82` now uses `server_default=text(f"'{DEFAULT_SCHEDULE_TIMEZONE}'")`. The migration correctly keeps its literal.
- **M2** (Open): `src/lib/schemas/schedule.py:28`: `zoneinfo.available_timezones()` still accepts non-zone entries such as `Factory`, and it unions the host `TZPATH` with the `tzdata` package. Research R3 still has no note on either point. This is optional, and a one-line note in R3 is enough.
- **M3** (Resolved): `_trigger_log_fields` (`src/lib/services/schedule_service.py:60-67`) builds the base dict, including `timezone`, once and overlays the `crons` fields.
- **M4** (Resolved): `WallClockCronTrigger.get_next_fire_time`, `InstantOrTrigger.get_next_fire_time` and both new `ScheduleRequest` validators (`default_null_timezone`, `validate_timezone`) have docstrings.
- **M5** (Open): the new tests in `tests/unit/lib/services/test_schedule_service.py`, `tests/unit/lib/repositories/test_schedule_repository.py`, `tests/unit/api/v1/endpoints/test_schedules.py` and `tests/unit/api/mcp/test_mcp_tools.py` still use `unittest.mock` directly, following those files' existing style. This is not a blocker. Prefer `mocker` for new code.
- **M6** (Open): `CLAUDE.md` (Commands section) still documents `make lint` / `make test`, but the branch has no `Makefile`. This predates the feature.
- **M7** (Open, new): `src/lib/services/trigger_factory.py:48-51`. The lower-bound expression always evaluates to `after_previous`: `lower = after_previous if lower < after_previous else min(lower, after_previous)`. If `lower >= after_previous`, `min(...)` is `after_previous` too, so the conditional is dead code. The comment "Mirrors CronTrigger" is also inaccurate. `CronTrigger` uses `min(now, previous + 1 µs)`, which can return a time before `previous` when `now < previous`. This trigger never does (probe: `prev=12:00Z, now=11:00Z, minute="*/10"` returns 12:10Z, where `CronTrigger` would return 11:00Z). APScheduler always calls with `previous <= now`, so behaviour is unaffected, and never going backwards is arguably the better choice. Simplify the code and state the actual rule:
  ```python
  lower = now.astimezone(UTC)
  if previous_fire_time is not None:
      # Catch up from just after the previous fire time and never repeat or precede it.
      lower = previous_fire_time.astimezone(UTC) + _TICK
  ```
  R5a's wording ("`min(now, previous + 1 µs)`, raised to `previous + 1 µs` if needed") reduces to the same thing and can be simplified the same way.
- **M8** (Open, new): `_earliest_instant_from` / `_reorder_window` (`trigger_factory.py:59-80`, `:163-169`). Within ±2 h of a change, each call scans about 2 h of wall-clock matches, one on each side of the first match. For sub-minute schedules this adds up. Measured: `second="*"` takes 32 ms per call and `second="*/10"` 4.3 ms per call. Each run computes the next fire time once on the scheduler's single processing thread, so an every-second schedule costs about 3% of that thread for about 4 h, twice a year. A few dozen such schedules would make the worker fall behind during that period. R5a documents the cost, and minute-level schedules are cheap (≤ 0.8 ms), so this is acceptable as shipped. If it matters, there are two options. One is to widen the scan only when the wall-clock interval `[naive(lower) - Δ, naive(lower)]` actually contains a gap or repeated span, rather than whenever a change falls within ±2 h. The other is a minimum-interval rule for the `second` field. Otherwise, a note in `api_contract.md` is enough.
- **M9** (Open, new): `_wall_clock_trigger` (`trigger_factory.py:102-107`) builds the stepper from a hand-written `__setstate__` dict with `"version": 2` hard-coded. Any APScheduler release has to keep reading version-2 states for stored jobs, so this works. Deriving the state from the library keeps the field set in step automatically and makes the intent clearer:
  ```python
  state = {**self.__getstate__(), "timezone": UTC, "start_date": None, "end_date": None, "jitter": None}
  stepper.__setstate__(state)
  ```
  This is optional. Note that `fields` is shared by reference, which is safe because APScheduler cron fields are stateless.
- **M10** (Open, new): the design artifacts still describe the superseded approach.
  - `plan.md:113` gets the candidate from `super()` and checks `str(field)` against `"*"`.
  - `tasks.md:138` says ``str(<hour field>) != "*"``.
  - `tasks.md:140` says "why `is_default` is not used (research R5)".
  - `research.md:38` (R5) recommends `str(hour_field) == "*"`.

  Add a one-line "superseded by R5a" pointer in each place, so the plan matches the code. Also consider aligning `spec.md` FR-013 and the Fall-back edge case ("Entries that fire every hour, or more often") with the implemented and documented rule ("entries whose hour matches all 24 hours"). See Intent Alignment.

## Fresh review: `src/lib/services/trigger_factory.py`

I re-reviewed the whole module. The design is sound and well documented. It treats APScheduler only as a naive wall-clock matcher, in UTC where no clock changes exist, and does all instant mapping and ordering by timestamp.

- **Mapping** (`_instants_for`): `fold=0`/`fold=1` offset comparison classifies normal, gap (`first < second`) and repeated (`first > second`) times purely by offset, so it also handles reverse changes (Casablanca) and 30-minute changes (Lord Howe). Gap times resolve with the pre-change offset, which is exactly the forward shift FR-012 requires. `_canonical` relabels shifted and second-pass instants with the correct offset.
- **Scan bounds**: the start offset (`naive(lower) - window(lower)`) and the horizon (`first_match + window(best)`) are both bounded by the change size Δ. With `_MAX_CLOCK_CHANGE = 2h`, this covers Antarctica/Troll's 2-hour change exactly at the boundary, and the oracle confirms it. When no change is within ±2 h, the window is 0 and the scan returns after the first match.
- **Bounds and options**: `start_date` is honoured on every call. A naive `start_date` inside the gap localises to the shifted instant, which the probe confirmed (`02:20` start, first fire `01:30Z`). `end_date` is compared by timestamp, and an end inside the repeated hour stops correctly. Jitter is applied once, at the outermost trigger. Coinciding child instants de-duplicate through `previous + 1 µs`.
- **Compatibility**: stored UTC `EntryCronTrigger` children from the 041 release load into the new subclass and behave the same as before (window is always 0 in UTC). Plain `CronTrigger` and `OrTrigger` rows from before this feature are untouched. Class names and modules are pinned with docstring notes, and pickle round trips are tested for all three classes.
- **Types and style**: full annotations, mypy clean, private helpers are module-level and small, comments explain only the non-obvious points (timestamp vs datetime comparison, `fold=0` gap resolution).
- **New findings**: M7 (dead conditional and inaccurate comment), M8 (cost near changes for sub-minute schedules), M9 (stepper state construction). No correctness defects found.

## Intent Alignment

- **FR-001/002/003/009/010/014**: met (unchanged since run 1).
- **FR-004/005**: met for `cron`, `crons` (any number of entries) and `date`, including DST days, which was the divergence in run 1.
- **FR-006/007/008/011/015**: met (unchanged since run 1). The docs now describe the all-24-hours rule.
- **FR-012 / SC-004**: met for single-entry and multi-entry schedules. Gap times fire once, shifted forward. No runs are missed or duplicated (oracle: 0 mismatches over 11 zones).
- **FR-013**: met under the rule recorded in `contracts/schedule-timezone.md`, `tasks.md:127` and research R5a: "every hour or more often" means the hour field matches all 24 hours. The spec text is broader. An entry such as `hour="1-3", minute="*/30"` fires more often than hourly within its window, yet it skips the second pass of the repeated hour (pinned by `test_narrow_hour_range_skips_second_pass_of_repeated_hour`). This is a deliberate and documented choice, not a bug. Tighten the wording in `spec.md` so spec and code agree (M10).
- **Plan design notes**: the trigger design now intentionally departs from `plan.md:113` (a `super()`-based candidate loop with `str(field) == "*"`) in favour of R5a. R5a records the rationale and verification. The plan text should point to it (M10). Everything else in plan design notes 1-8 is followed as in run 1.

## Checklist Pass

- **Correctness & Logic**: pass. C1 and I1 are resolved. The rewritten trigger matched an independent oracle in 4,675 checks. Remaining items are clarity only (M7).
- **Type Safety**: pass (mypy clean).
- **Code Style & Conventions**: pass (ruff check and format clean, docstrings present, no `print`, `__init__.py` untouched).
- **Architecture & Design**: pass. The logic stays in `lib/services/trigger_factory.py`, and the worker needs no change. M8 and M9 are optional refinements.
- **Database & ORM**: pass (unchanged since run 1, and M1 is resolved).
- **Project Structure**: pass.
- **Testing**: strong. 1587 unit and 240 integration tests pass, and multi-cron DST coverage now exists. Two regression pins are still missing (I2), and the `unittest.mock` style remains (M5).
- **Security**: pass.

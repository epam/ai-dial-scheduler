# Feature Specification: Per-Schedule Timezone

**Feature Branch**: `042-schedule-timezone`

**Created**: 2026-11-04

**Status**: Draft

**Input**: User description: "On create and update, accept a timezone field from the frontend, return it on GET, store it in the database for the schedule, and evaluate the schedule's cron in that timezone."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Schedule runs at local wall-clock time (Priority: P1)

A user in Berlin wants a report every weekday at 09:00 local time. Today every schedule is evaluated in UTC, so the user has to convert 09:00 to UTC by hand. Worse, the converted value is only right for half of the year: after a daylight-saving change the report arrives an hour early or late. The user picks their timezone in the frontend, which sends it with the schedule, and the schedule fires at 09:00 Berlin time all year round.

**Why this priority**: This is the whole feature. Without it, every user outside UTC gets runs that drift by an hour twice a year.

**Independent Test**: Create a schedule "every day at 09:00" with timezone `Europe/Berlin` and verify its upcoming run times are 08:00 UTC before the spring daylight-saving change and 07:00 UTC after it.

**Acceptance Scenarios**:

1. **Given** a create request with a cron trigger "daily at 09:00" and timezone `Europe/Berlin`, **When** the schedule is created, **Then** its next run time is the next 09:00 in Berlin, expressed as the equivalent UTC instant.
2. **Given** that schedule, **When** Berlin switches from winter to summer time, **Then** the schedule keeps firing at 09:00 Berlin time, so the UTC instant moves from 08:00 to 07:00.
3. **Given** a schedule with a list of cron entries (Monday 08:00 and Friday 10:00) and timezone `America/New_York`, **When** it runs, **Then** both entries fire at those local times in New York.
4. **Given** a schedule with timezone `Asia/Kolkata` (UTC+05:30), **When** it fires daily at 09:00, **Then** the run happens at 03:30 UTC. Non-whole-hour offsets are honoured.

---

### User Story 2 - Frontend round-trips the timezone unchanged (Priority: P1)

The frontend offers the timezones the browser knows (`Intl.supportedValuesOf('timeZone')`) and sends the chosen value. The user later opens the schedule to edit it, and the frontend must preselect exactly the value it sent. Browsers return some older names, such as `Europe/Kiev` or `Asia/Calcutta`, so the server must accept them and return them as they were sent, without renaming.

**Why this priority**: If a value the browser offers is rejected, or comes back renamed, the edit form breaks. This is as important as the evaluation itself.

**Independent Test**: Submit a schedule with each timezone the browser offers and verify each is accepted, and that detail and list responses return exactly the submitted string.

**Acceptance Scenarios**:

1. **Given** a create request with timezone `Europe/Kiev`, **When** the schedule is created and then retrieved, **Then** the response carries `Europe/Kiev`, not a renamed equivalent.
2. **Given** a create request with timezone `Europe/Kyiv` (the current name for the same zone), **When** submitted, **Then** it is accepted as well and fires at the same instants as `Europe/Kiev`.
3. **Given** any schedule, **When** it is returned by create, update, detail or list, **Then** the response includes its timezone.

---

### User Story 3 - Change a schedule's timezone (Priority: P2)

A user moves to another city or picked the wrong zone. They update the schedule with a new timezone, and the schedule's future runs follow the new zone from then on.

**Why this priority**: Editing is needed to correct mistakes, but creation alone already delivers the value.

**Independent Test**: Update a "daily 09:00 UTC" schedule to timezone `Asia/Tokyo` and verify the next run time becomes the next 09:00 Tokyo time (00:00 UTC).

**Acceptance Scenarios**:

1. **Given** an existing schedule in UTC, **When** it is updated with timezone `Asia/Tokyo` and the same cron, **Then** its next run time is recalculated as the next 09:00 in Tokyo.
2. **Given** a paused schedule, **When** its timezone is changed and it is resumed, **Then** its next run time is computed in the new timezone.
3. **Given** a schedule with timezone `Europe/Berlin`, **When** it is updated with a body that omits the timezone, **Then** the timezone becomes UTC, the same as any other omitted optional field in an update (see Assumptions).

---

### User Story 4 - Existing schedules and clients keep working (Priority: P2)

Clients that do not know about timezones keep sending requests in the current format. Schedules created before this feature keep firing at exactly the same UTC instants.

**Why this priority**: Backward compatibility protects every existing schedule and integration, including MCP clients and scripts.

**Independent Test**: Retrieve and run a schedule created before this feature, and create one without a timezone. Verify both report timezone `UTC` and fire at the same instants as before.

**Acceptance Scenarios**:

1. **Given** a schedule stored before this feature, **When** it is retrieved, **Then** it reports timezone `UTC` and fires at the same instants as before.
2. **Given** a create request without a timezone, **When** submitted, **Then** the schedule is stored with timezone `UTC`.
3. **Given** a create request with timezone `UTC`, **When** submitted, **Then** it is accepted even though browsers do not list `UTC` among their timezones.

---

### User Story 5 - Clear feedback on an invalid timezone (Priority: P3)

A user or tool that sends an unknown or malformed timezone gets an error that points at the timezone field. Nothing is stored or scheduled.

**Why this priority**: Good errors reduce trial and error but do not change what the feature can do.

**Independent Test**: Submit `Mars/Olympus`, `+02:00`, `europe/berlin` and an empty string, and verify each is rejected with a validation error located at the timezone field.

**Acceptance Scenarios**:

1. **Given** a request with timezone `Mars/Olympus`, **When** submitted, **Then** it is rejected with a validation error that names the timezone field, and no schedule is created or changed.
2. **Given** a request with a UTC offset such as `+02:00` instead of a zone name, **When** submitted, **Then** it is rejected with a validation error.
3. **Given** a request with `europe/berlin` (wrong letter case), **When** submitted, **Then** it is rejected the same way on every platform where the service runs.

---

### Edge Cases

- **Spring forward (missing local time)**: a run planned for a local time that does not exist that day, such as 02:30 in Berlin on the last Sunday of March, fires exactly once, shifted forward by the length of the gap (03:30 local). It is never skipped.
- **Fall back (repeated local time)**: a run planned for a specific local time that occurs twice, such as 02:30 in Berlin on the last Sunday of October, fires exactly once, at the first occurrence. Entries that fire in every hour of the day (their hour rule covers all 24 hours) keep firing on every real elapsed interval through the repeated hour, so they are neither duplicated nor skipped. An entry limited to some hours (for example 01:00–03:00 every 30 minutes) fires only on the first pass.
- **Date and time values without an offset** (cron `start_date`/`end_date`, and a one-off `date` trigger) are interpreted in the schedule's timezone. Values that carry an explicit offset or `Z` keep their absolute meaning. For schedules in UTC this is the same as today.
- **Multi-cron schedules**: one timezone applies to every entry of the list. Entries cannot have their own timezones.
- **One-off date triggers** may carry a timezone too. It affects only how a date without an offset is interpreted.
- **Responses keep timestamps in UTC**: `next_run_time`, run times and audit timestamps keep their current format. The timezone controls when the schedule fires, not how timestamps are serialised.
- **Timezone rules change** (a country abolishes daylight saving time, for example): future runs follow the updated rules once the service runs with an updated timezone database. Stored schedules need no change.
- **Legacy and current names for the same zone** (`Europe/Kiev` and `Europe/Kyiv`) are both valid, fire at the same instants, and are each returned as submitted.
- **`null` timezone** in a request is treated the same as an omitted timezone, so it means UTC.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Create and update requests MUST accept an optional timezone for the schedule, given as an IANA Time Zone Database identifier (for example `Europe/Berlin`).
- **FR-002**: The timezone MUST be stored with the schedule and persist across restarts of the API and worker services.
- **FR-003**: Create, update, detail and list responses MUST return the schedule's timezone exactly as it was submitted, with no renaming or normalisation.
- **FR-004**: Cron triggers, including every entry of a list trigger, MUST be evaluated in the schedule's timezone, so that runs happen at the configured local wall-clock time across daylight-saving changes.
- **FR-005**: Date and time values without an explicit offset in a trigger (cron start and end dates, one-off date) MUST be interpreted in the schedule's timezone. Values with an explicit offset MUST keep their absolute meaning.
- **FR-006**: The accepted set MUST include every timezone offered by current major browsers through `Intl.supportedValuesOf('timeZone')`, including legacy aliases such as `Europe/Kiev`, `Asia/Calcutta` and `Asia/Saigon`, and MUST also include `UTC`.
- **FR-007**: Unknown identifiers, UTC offsets (such as `+02:00`), empty strings and identifiers with the wrong letter case MUST be rejected with a validation error located at the timezone field. Validation MUST give the same result on every platform where the service runs.
- **FR-008**: A rejected request MUST NOT create, change or reschedule any schedule.
- **FR-009**: When the timezone is omitted or `null`, the schedule MUST use UTC.
- **FR-010**: Schedules stored before this feature MUST report timezone `UTC` and MUST keep firing at the same UTC instants, with no manual migration step for operators.
- **FR-011**: Changing a schedule's timezone through an update MUST recalculate its next run time in the new timezone. A paused schedule MUST use its current timezone when resumed.
- **FR-012**: A local time that does not exist because of a daylight-saving change MUST fire exactly once, shifted forward by the length of the gap.
- **FR-013**: A specific local time that occurs twice because of a daylight-saving change MUST fire exactly once, at its first occurrence. Entries whose hour rule covers all 24 hours MUST fire on every real elapsed interval through the repeated hour; entries limited to some hours fire only on the first pass.
- **FR-014**: The reported next run time MUST stay an absolute instant in the existing response format.
- **FR-015**: The API contract documentation, the OpenAPI examples, the MCP tool descriptions and the changelog MUST describe the timezone field, its default, the accepted values and the daylight-saving behaviour. Descriptions that say "all times are UTC" MUST be updated.

### Key Entities

- **Schedule**: Gains a timezone, an IANA identifier that defaults to `UTC`. It applies to the schedule's whole trigger.
- **Trigger**: A single cron, a list of crons or a one-off date. Unchanged in shape. Its local times are now read in the schedule's timezone.
- **Run**: Unchanged. Run times stay absolute UTC instants.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A "weekdays at 09:00, `Europe/Berlin`" schedule fires at 09:00 Berlin time on 100% of weekdays over a window that includes both daylight-saving changes of a year.
- **SC-002**: 100% of the timezones returned by `Intl.supportedValuesOf('timeZone')` in current Chrome (418 values in the reference run on Node 25 with ICU 78.3), plus `UTC`, are accepted and returned unchanged.
- **SC-003**: 100% of schedules created before this feature keep firing at the same UTC instants after the release.
- **SC-004**: On the daylight-saving change days, a fixed-time daily schedule produces 0 duplicate runs and 0 missed runs.
- **SC-005**: 100% of requests with an invalid timezone are rejected before anything is stored, with an error that names the timezone field.

## Assumptions

- The timezone is a property of the schedule, not of each cron entry. One schedule has one timezone.
- Update keeps its current full-replacement behaviour: an update body that omits the timezone resets it to UTC, just as omitting other optional fields resets them. The frontend is expected to always send the timezone on update.
- The frontend sends IANA identifiers obtained from the browser. Showing timezones by display name or offset is a frontend concern.
- Timestamps in responses stay in UTC. Converting them to local time for display is a frontend concern.
- Filtering or sorting the schedule list by timezone is out of scope.
- Per-entry timezones, raw UTC offsets and timezone abbreviations as user input are out of scope.
- The service relies on an IANA timezone database that is kept current through normal dependency updates, and that does not depend on the host operating system.
- Access control for creating, updating and reading schedules is unchanged.

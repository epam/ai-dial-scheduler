# Feature Specification: Multiple Cron Triggers per Schedule

**Feature Branch**: `041-multi-cron-triggers`

**Created**: 2026-10-30

**Status**: Draft

**Input**: User description: "Support multiple cron triggers per schedule"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run one schedule at several recurring times (Priority: P1)

A user wants a single schedule to fire at different times on different days, for example "every Monday at 08:00 and every Friday at 10:00". Today a schedule accepts exactly one cron specification, and a single cron cannot express different hours on different weekdays. The user would have to create several schedules and manage them separately. The user supplies a list of cron specifications when creating a schedule, and the schedule fires whenever any entry in the list fires.

**Why this priority**: This is the whole feature. It removes the need to duplicate schedules for irregular timetables.

**Independent Test**: Create a schedule with two cron entries (Monday 08:00 and Friday 10:00 UTC) and verify its upcoming run times alternate between those two moments and no others.

**Acceptance Scenarios**:

1. **Given** a create request whose trigger lists a Monday 08:00 entry and a Friday 10:00 entry, **When** the schedule is created, **Then** it is stored as one schedule with one id and its next run time is the earliest upcoming of the two.
2. **Given** that schedule and the current time is Friday 11:00 UTC, **When** the next run time is requested, **Then** it is the following Monday 08:00 UTC.
3. **Given** that schedule, **When** it runs at Monday 08:00 and again at Friday 10:00, **Then** both runs appear in the same run history under the one schedule id.
4. **Given** a list trigger whose entries fire at the same instant, **When** that instant arrives, **Then** the schedule runs exactly once.

---

### User Story 2 - Manage a multi-cron schedule like any other (Priority: P2)

A user changes, pauses, resumes and deletes a multi-cron schedule through the same operations used for single-trigger schedules. A user can also change an existing schedule's trigger between a single cron, a list of crons and a one-off date.

**Why this priority**: The list form is only useful if it is manageable after creation, but creation alone already delivers value.

**Independent Test**: Update an existing single-cron schedule to a two-entry list, pause it, resume it, then delete it. Verify each step behaves as it does for single-cron schedules.

**Acceptance Scenarios**:

1. **Given** an existing single-cron schedule, **When** the user updates it with a list of cron entries, **Then** the schedule fires only at times defined by the new list.
2. **Given** a multi-cron schedule, **When** the user updates it with a one-off date trigger, **Then** it fires only at that date.
3. **Given** a multi-cron schedule, **When** it is paused, **Then** it has no upcoming run time and does not fire. **When** it is resumed, **Then** its next run time is the earliest upcoming fire time across all entries.
4. **Given** a multi-cron schedule, **When** it is deleted, **Then** none of its entries fire any longer.

---

### User Story 3 - Existing schedules and clients keep working (Priority: P2)

A user or tool that already sends or stores a single cron specification continues to work with no change in request format, stored data or behavior.

**Why this priority**: Backward compatibility protects every existing schedule and integration, including automated clients that cannot be updated in lockstep.

**Independent Test**: Read, update and run a schedule created before this feature, and create a new schedule using the original single-cron format. Verify behavior is identical to before.

**Acceptance Scenarios**:

1. **Given** a schedule created with a single cron before this feature, **When** it is retrieved or listed, **Then** it is returned and executes as before.
2. **Given** a create request using the original single-cron format, **When** it is submitted, **Then** it is accepted and behaves as before.
3. **Given** a list containing exactly one entry, **When** the schedule runs, **Then** it behaves the same as a single-cron schedule with that entry.

---

### User Story 4 - Clear feedback on invalid triggers (Priority: P3)

A user who submits an invalid trigger gets an error that says what is wrong, including which list entry is at fault.

**Why this priority**: Good errors reduce trial and error but do not change what the feature can do.

**Independent Test**: Submit a list with one invalid entry among valid ones and verify the error identifies that entry.

**Acceptance Scenarios**:

1. **Given** a trigger with an empty list of cron entries, **When** submitted, **Then** it is rejected with a validation error.
2. **Given** a list in which the second entry has an invalid field value, **When** submitted, **Then** it is rejected and the error identifies the second entry.
3. **Given** a trigger that sets more than one kind (a single cron, a list of crons, or a date), **When** submitted, **Then** it is rejected with a validation error.

---

### Edge Cases

- Entries in the list overlap (for example an hourly entry and a daily 08:00 entry): the schedule runs once per distinct fire time, never twice for the same instant.
- Entries carry different start and end dates: each entry honours its own bounds, and the schedule's next run time ignores entries whose bounds have passed.
- All entries have expired (every end date is in the past): the schedule has no upcoming run time, the same as an expired single-cron schedule.
- The list contains duplicate entries: the schedule runs once per fire time. Duplicates are accepted and not treated as errors.
- A list with a very large number of entries: a documented upper limit applies and requests above it are rejected (see FR-012).
- Updating with a list replaces the prior trigger entirely. Entries are never merged with the previous trigger.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to create a schedule whose trigger is a list of one or more cron specifications.
- **FR-002**: Each cron specification in the list MUST accept the same fields and optional start and end dates as today's single cron specification.
- **FR-003**: A schedule with a list trigger MUST fire whenever any entry in the list fires, and MUST remain a single schedule with one id, one run history and one pause/resume/delete lifecycle.
- **FR-004**: When several entries fire at the same instant, the schedule MUST run exactly once for that instant.
- **FR-005**: The schedule's reported next run time MUST be the earliest upcoming fire time across all entries.
- **FR-006**: The list MUST contain at least one entry, and an empty list MUST be rejected with a validation error.
- **FR-007**: A trigger MUST define exactly one kind: a single cron, a list of crons, or a date. Defining more than one, or none, MUST be rejected.
- **FR-008**: Each entry MUST be validated independently, and a validation error MUST identify the entry at fault.
- **FR-009**: Requests that use the existing single-cron format MUST continue to be accepted and behave exactly as before.
- **FR-010**: Existing stored schedules MUST continue to be returned and executed correctly without any data migration or rewriting.
- **FR-011**: Updating a schedule MUST allow switching its trigger between a single cron, a list of crons and a date, with the new trigger fully replacing the old one.
- **FR-012**: The number of entries in a list MUST be bounded by a documented maximum, and requests exceeding it MUST be rejected with a validation error.
- **FR-013**: All fire times MUST continue to be evaluated in UTC.
- **FR-014**: List and detail responses MUST report the trigger type as "cron" for schedules with either a single cron or a list of crons, and MUST return the trigger with the same content that was submitted. Trigger kinds that were not used MAY appear as `null` or be absent, and clients MUST treat both the same.
- **FR-015**: The API contract documentation, the OpenAPI examples and the MCP tool descriptions MUST be updated to describe the list form.

### Key Entities

- **Schedule**: A named, recurring or one-off job definition with one trigger. Its trigger is now one of: a single cron specification, a list of cron specifications, or a date.
- **Cron specification**: A set of time fields (such as day of week, hour and minute) with optional start and end date bounds. A schedule may now hold several.
- **Run**: A record of one execution of a schedule. Runs from all entries of a multi-cron schedule share the one schedule's run history.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can express "Monday 08:00 and Friday 10:00" as one schedule, and over any four-week window that schedule fires exactly eight times at exactly those moments.
- **SC-002**: A user who previously needed N separate schedules for N recurring times needs one, and pause, resume and delete take one action instead of N.
- **SC-003**: 100% of schedules created before this feature are returned and executed identically after it, with no data changes required.
- **SC-004**: When multiple entries coincide, 0 duplicate runs occur for the same instant.
- **SC-005**: An invalid trigger submission produces an error that names the offending entry in 100% of cases where the fault is within a single entry.

## Assumptions

- The list form is a new, additive option. The existing single-cron format remains supported indefinitely.
- All times remain UTC. Per-schedule timezone support is out of scope.
- A schedule's trigger is either date-based or cron-based. Mixing a date and cron entries in one schedule is out of scope.
- Entries use the existing structured field format. Raw cron-expression strings are out of scope.
- The maximum number of entries is a modest fixed value, to be decided during planning, chosen so that realistic timetables are never constrained.
- Each entry keeps its own start and end dates, since that follows directly from reusing the existing specification shape for each entry.
- Users are the existing schedule owners, and access control for create, update, pause, resume and delete is unchanged.

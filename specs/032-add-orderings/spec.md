# Feature Specification: Add Ordering for Schedules/Runs List

**Feature Branch**: `032-add-orderings`

**Created**: 2026-08-03

**Status**: Draft

**Input**: User description: "Add ordering for schedules/runs list. Schedules list: by creation date asc/desc, by next run date asc/desc (put inactive runs to the end), by name asc/desc. Run list: by creation date asc/desc."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Order schedules by creation date (Priority: P1)

A user viewing their list of schedules wants to sort the list by when each schedule was created, either oldest-first or newest-first, so they can find recently added schedules or review historical ones.

**Why this priority**: Creation-date ordering is the most common and predictable sort users expect from any list, and it applies uniformly to every schedule regardless of its state. It provides immediate value on its own.

**Independent Test**: Request the schedules list ordered by creation date ascending, then descending, and confirm the returned schedules appear in the correct chronological order in each case.

**Acceptance Scenarios**:

1. **Given** a user has multiple schedules created at different times, **When** they request the schedules list ordered by creation date ascending, **Then** schedules are returned from oldest to newest.
2. **Given** a user has multiple schedules created at different times, **When** they request the schedules list ordered by creation date descending, **Then** schedules are returned from newest to oldest.
3. **Given** a user requests the schedules list without specifying an order, **When** the list is returned, **Then** a consistent default ordering is applied.

---

### User Story 2 - Order schedules by next run date (Priority: P2)

A user viewing their list of schedules wants to sort by the time each schedule is next scheduled to run, either soonest-first or latest-first, so they can see what is coming up. Schedules that have no upcoming run (e.g. inactive/paused schedules) are always placed at the end of the list regardless of the ascending/descending direction.

**Why this priority**: Next-run ordering helps users plan around upcoming executions, but it depends on correctly handling schedules that have no next run time, which adds complexity beyond simple creation-date ordering.

**Independent Test**: Create a mix of active schedules with different next run times and inactive schedules with no next run time, request the list ordered by next run date ascending and descending, and confirm active schedules are ordered correctly while all schedules without a next run appear at the end.

**Acceptance Scenarios**:

1. **Given** a user has active schedules with different next run times, **When** they request the list ordered by next run date ascending, **Then** schedules are returned from soonest to latest next run.
2. **Given** a user has active schedules with different next run times, **When** they request the list ordered by next run date descending, **Then** active schedules are returned from latest to soonest next run.
3. **Given** a user has a mix of active schedules and inactive schedules with no next run time, **When** they request the list ordered by next run date ascending, **Then** all schedules with no next run time appear after all schedules that have a next run time.
4. **Given** a user has a mix of active schedules and inactive schedules with no next run time, **When** they request the list ordered by next run date descending, **Then** all schedules with no next run time still appear at the end of the list.

---

### User Story 3 - Order schedules by name (Priority: P2)

A user viewing their list of schedules wants to sort the list alphabetically by the schedule's name, either A→Z or Z→A, so they can locate a specific schedule by name.

**Why this priority**: Name ordering is a common, intuitive way to find a known schedule in a long list, and every schedule always has a name, so it applies uniformly. It sits alongside next-run ordering in value.

**Independent Test**: Request the schedules list ordered by name ascending, then descending, and confirm the returned schedules appear in the correct alphabetical order in each case.

**Acceptance Scenarios**:

1. **Given** a user has multiple schedules with different names, **When** they request the schedules list ordered by name ascending, **Then** schedules are returned in ascending alphabetical order of their name.
2. **Given** a user has multiple schedules with different names, **When** they request the schedules list ordered by name descending, **Then** schedules are returned in descending alphabetical order of their name.
3. **Given** a user has schedules whose names differ only in letter case, **When** they request the list ordered by name, **Then** ordering is applied consistently and deterministically.

---

### User Story 4 - Order runs by creation date (Priority: P3)

A user viewing the list of runs for a schedule wants to sort the list by when each run was created, either oldest-first or newest-first, so they can review the run history in the order that suits their task.

**Why this priority**: Run history ordering is valuable but secondary to the schedules list, which is the primary entry point; run lists are typically viewed within the context of a single schedule.

**Independent Test**: Request the runs list ordered by creation date ascending, then descending, and confirm the runs appear in the correct chronological order in each case.

**Acceptance Scenarios**:

1. **Given** a schedule has multiple runs created at different times, **When** the user requests the runs list ordered by creation date ascending, **Then** runs are returned from oldest to newest.
2. **Given** a schedule has multiple runs created at different times, **When** the user requests the runs list ordered by creation date descending, **Then** runs are returned from newest to oldest.
3. **Given** the user requests the runs list without specifying an order, **When** the list is returned, **Then** a consistent default ordering is applied.

---

### Edge Cases

- When two or more items share the same sort value (identical creation timestamps, identical next run times, or identical names), the ordering between them MUST remain stable and deterministic so pagination does not skip or duplicate items.
- When schedule names differ only by letter case, ordering by name MUST be applied consistently (a defined, documented case-handling rule) and remain deterministic.
- When all schedules are inactive (none have a next run time), ordering by next run date returns all of them in a consistent secondary order.
- When an invalid or unsupported ordering value is requested, the system rejects the request with a clear error rather than silently ignoring it.
- Ordering MUST behave correctly together with existing filtering and pagination so that the applied filters and page boundaries respect the chosen order.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow the schedules list to be ordered by creation date in ascending or descending direction.
- **FR-002**: System MUST allow the schedules list to be ordered by next run date in ascending or descending direction.
- **FR-003**: When ordering the schedules list by next run date, System MUST place all schedules that have no next run time (e.g. inactive/paused schedules) at the end of the list, regardless of the ascending or descending direction.
- **FR-004**: System MUST allow the schedules list to be ordered by name in ascending or descending alphabetical direction.
- **FR-005**: System MUST allow the runs list to be ordered by creation date in ascending or descending direction.
- **FR-006**: System MUST apply a consistent, documented default ordering when the caller does not specify an ordering for the schedules list and for the runs list.
- **FR-007**: System MUST produce a stable, deterministic order for items that share the same primary sort value, so that pagination remains consistent across requests.
- **FR-008**: System MUST reject requests that specify an unsupported ordering field or direction with a clear validation error.
- **FR-009**: Ordering MUST work in combination with existing list filtering and pagination without altering which items match the filters.

### Key Entities *(include if feature involves data)*

- **Schedule**: A user-owned scheduled task. Relevant attributes for this feature: creation date, next run time (which may be absent when the schedule is inactive/paused), active/inactive state, and name.
- **Run**: A record of a single execution attempt belonging to a schedule. Relevant attribute for this feature: creation date.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can order the schedules list by creation date in both directions and, in 100% of cases, the returned items match the requested chronological order.
- **SC-002**: A user can order the schedules list by next run date in both directions, and in 100% of cases every schedule without a next run time appears after all schedules that have one.
- **SC-003**: A user can order the schedules list by name in both directions and, in 100% of cases, the returned items match the requested alphabetical order.
- **SC-004**: A user can order the runs list by creation date in both directions and, in 100% of cases, the returned items match the requested chronological order.
- **SC-005**: Repeated requests for the same ordered list (including across paginated pages) return items in the same order with no duplicates or omissions in 100% of cases.
- **SC-006**: Requests with an unsupported ordering value are rejected with a clear error in 100% of cases, with no unordered or partially ordered results returned.

## Assumptions

- Ordering applies to the existing schedules list and runs list capabilities; no new list surfaces are introduced by this feature.
- "Next run date" refers to the time a schedule is next expected to execute; inactive or paused schedules are treated as having no next run time.
- Default ordering, when none is specified, is by creation date descending (newest first), consistent with common list expectations; this preserves current behavior if it already matches.
- "Name" refers to the schedule's display name. Name ordering is case-insensitive alphabetical order.
- Ordering by name applies to schedules only; runs are always scoped to a single schedule and have no independent name to sort by.
- Ascending/descending direction is independently selectable for each supported ordering field.
- Ordering is a per-request option and is not persisted as a user preference.
- Existing filtering and pagination behavior on these lists is retained and combined with ordering.

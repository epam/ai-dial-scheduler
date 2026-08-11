# Feature Specification: Support Schedules Delete

**Feature Branch**: `035-schedule-delete`

**Created**: 2026-08-10

**Status**: Draft

**Input**: User description: "Support schedules delete. For schedules add is_deleted field. Field is visible on list and details. Field not editable via PUT. List is filtered by is_deleted field. Add schedule DELETE Endpoint with logic: if no runs for this schedule exist - delete apscheduler record and schedule record from DB; if any run for schedule present - delete apscheduler record, mark schedule is_deleted. There is no way to restore schedule from deleted/soft deleted."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Delete a schedule that has never run (Priority: P1)

A user who created a schedule by mistake, or who no longer needs a schedule that has not yet produced any execution history, deletes it. Because there is no history worth keeping, the schedule disappears entirely: it stops being scheduled and it is no longer listed or retrievable at all.

**Why this priority**: This is the core capability being requested and the most common case for newly created or misconfigured schedules. On its own it lets users clean up unwanted schedules, which is a complete, valuable slice.

**Independent Test**: Create a schedule, do not let it execute, delete it, then confirm it no longer appears in the schedules list, cannot be retrieved individually, and never executes again.

**Acceptance Scenarios**:

1. **Given** a user owns a schedule that has no run history, **When** they delete it, **Then** the request succeeds and the schedule is removed completely.
2. **Given** a schedule with no run history has been deleted, **When** the user requests the schedules list, **Then** the schedule does not appear in the results under any deletion filter, including a filter that asks for deleted schedules.
3. **Given** a schedule with no run history has been deleted, **When** the user requests that schedule's details, **Then** the system responds that the schedule does not exist.
4. **Given** a schedule with no run history has been deleted, **When** its next scheduled execution time arrives, **Then** no execution occurs and no run record is produced.
5. **Given** a user attempts to delete a schedule that does not exist or that belongs to another user, **When** the delete request is made, **Then** the system responds that the schedule does not exist and nothing is changed.

---

### User Story 2 - Delete a schedule that has run history (Priority: P1)

A user deletes a schedule that has already executed at least once. The schedule stops executing immediately and is presented as deleted, but its accumulated run history remains intact and auditable, because past executions are records of work that actually happened.

**Why this priority**: Equally critical to Story 1 — without it, deleting any schedule that has ever run would either fail or destroy audit history. Together with Story 1 it makes deletion safe for every schedule regardless of age.

**Independent Test**: Create a schedule, let it produce at least one run, delete it, then confirm it is marked deleted, no longer executes, and its run history is still retrievable.

**Acceptance Scenarios**:

1. **Given** a user owns a schedule that has at least one run record, **When** they delete it, **Then** the request succeeds and the schedule is marked as deleted rather than removed.
2. **Given** a schedule with run history has been deleted, **When** its next scheduled execution time arrives, **Then** no execution occurs and no new run record is produced.
3. **Given** a schedule with run history has been deleted, **When** the user requests that schedule's run history, **Then** all previously recorded runs are still returned unchanged.
4. **Given** a schedule with run history has been deleted, **When** the user requests that schedule's details, **Then** the schedule is returned with its deletion state indicating it is deleted, and it reports no upcoming execution time.

---

### User Story 3 - See and filter by deletion state (Priority: P2)

A user browsing their schedules can tell which schedules are deleted and which are live. By default the list shows only live schedules so the working view stays clean, and the user can explicitly ask to see deleted schedules when reviewing history.

**Why this priority**: Visibility and filtering make the soft-deleted state comprehensible instead of confusing, but deletion itself already delivers value without it.

**Independent Test**: Create two schedules, let one produce a run and delete it, then request the list with the default filter, with the filter set to deleted, and confirm each response contains exactly the expected schedules and that every returned item exposes its deletion state.

**Acceptance Scenarios**:

1. **Given** a user owns both live and soft-deleted schedules, **When** they request the schedules list without specifying a deletion filter, **Then** only live schedules are returned.
2. **Given** a user owns both live and soft-deleted schedules, **When** they request the list filtered to deleted schedules, **Then** only soft-deleted schedules are returned.
3. **Given** any schedules list response, **When** the user inspects a returned item, **Then** the item includes its deletion state.
4. **Given** any single-schedule details response, **When** the user inspects it, **Then** it includes the schedule's deletion state.
5. **Given** a user owns soft-deleted schedules, **When** they request the list with the deletion filter combined with existing name, metadata, ordering, and pagination options, **Then** the deletion filter is applied together with the other options and the reported total count reflects the deletion filter.

---

### User Story 4 - Deleted schedules cannot be changed or restored (Priority: P2)

Once a schedule is deleted, deletion is final. A user cannot edit it, pause it, resume it, or bring it back to a live state, so a deleted schedule can never silently start executing again.

**Why this priority**: Protects the guarantee that deletion actually stops work. Without it, a deleted schedule could be reactivated through an existing edit or resume path, defeating the purpose of the feature.

**Independent Test**: Soft-delete a schedule, then attempt to update, pause, resume, and delete it again, and confirm each attempt is rejected and leaves the schedule deleted and non-executing.

**Acceptance Scenarios**:

1. **Given** a soft-deleted schedule, **When** the user attempts to update it, **Then** the request is rejected and the schedule remains deleted and non-executing.
2. **Given** a soft-deleted schedule, **When** the user attempts to resume or pause it, **Then** the request is rejected and the schedule remains deleted and non-executing.
3. **Given** a soft-deleted schedule, **When** the user attempts to delete it again, **Then** the request is rejected as referring to a schedule that is already deleted, and nothing changes.
4. **Given** any create or update request, **When** the caller includes a deletion-state value in the request body, **Then** the value is disregarded and the schedule's deletion state is determined solely by the system.
5. **Given** a user creates a new schedule, **When** it is created, **Then** its deletion state indicates it is not deleted.

---

### Edge Cases

- A run is recorded for a schedule between the moment the system observes that the schedule has no runs and the moment it removes the schedule. The outcome MUST remain consistent: either the schedule is soft-deleted with its new run preserved, or the deletion is reported as failed, never a state where run history is destroyed or orphaned.
- An execution is already in progress when the delete request arrives. That in-progress run MUST be allowed to finish and record its outcome, and the schedule MUST end up soft-deleted (not removed) because a run record exists for it.
- The schedule exists in the database but has no corresponding entry in the scheduling engine (for example it was already unregistered, or the record was never registered). Deletion MUST still succeed and complete the database side of the operation.
- The scheduling engine cannot be reached or fails to unregister the schedule. Deletion MUST NOT report success while the schedule could still execute; the caller MUST receive an error and the schedule MUST remain in a state where a retried delete succeeds.
- The database update fails after the schedule has been unregistered from the scheduling engine. The system MUST NOT leave a schedule that is presented as live but can never execute; the caller MUST receive an error and a retried delete MUST be able to complete the operation.
- Two delete requests for the same schedule arrive concurrently. Exactly one MUST be treated as the successful deletion; the other MUST be rejected without corrupting the schedule or its history.
- A schedule that has already been deleted while paused MUST behave identically to one deleted while active.
- Deleting a schedule MUST NOT affect any other schedule owned by the same user or by other users, and MUST NOT alter any other schedule's run history.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a deletion operation that a schedule's owner can invoke for a single schedule they own, identified by that schedule's identifier.
- **FR-002**: System MUST reject a deletion request for a schedule that does not exist, or that is owned by a different user, as referring to a non-existent schedule, and MUST make no changes.
- **FR-003**: When the targeted schedule has no run records, System MUST stop the schedule from ever executing again and MUST remove the schedule record entirely, so that it is no longer returned by any list or details request.
- **FR-004**: When the targeted schedule has at least one run record, System MUST stop the schedule from ever executing again and MUST mark the schedule as deleted while retaining the schedule record and all of its run records unchanged.
- **FR-005**: System MUST determine which of the two deletion behaviours applies based solely on whether any run record exists for that schedule at the time of deletion, with no user-supplied choice.
- **FR-006**: System MUST guarantee that a deleted schedule produces no further executions and no further run records, in both the removal and the mark-as-deleted case.
- **FR-007**: System MUST persist a deletion state for every schedule, which is set to "not deleted" when a schedule is created.
- **FR-008**: System MUST include each schedule's deletion state in every schedules list response item and in every single-schedule details response.
- **FR-009**: System MUST ignore any deletion-state value supplied in a create or update request body; the deletion state MUST be settable only by the deletion operation and MUST never be modified by an update.
- **FR-010**: System MUST allow the schedules list to be filtered by deletion state, returning only live schedules or only soft-deleted schedules.
- **FR-011**: System MUST return only live schedules from the schedules list when the caller does not specify a deletion filter.
- **FR-012**: The deletion filter MUST work in combination with existing name filtering, metadata filtering, ordering, and pagination, and the total count reported with a paginated list MUST reflect the deletion filter.
- **FR-013**: System MUST reject any attempt to update, pause, or resume a soft-deleted schedule, leaving it deleted and non-executing.
- **FR-014**: System MUST provide no operation, parameter, or filter value that restores a soft-deleted schedule to a live state, and MUST provide no way to recover a removed schedule.
- **FR-015**: System MUST reject a deletion request targeting an already soft-deleted schedule and MUST leave that schedule unchanged.
- **FR-016**: System MUST continue to return the run history of a soft-deleted schedule to its owner, unchanged, after deletion.
- **FR-017**: System MUST NOT report a deletion as successful unless the schedule is guaranteed both to be unable to execute again and to be recorded as deleted or removed; if either part cannot be completed, System MUST report an error and MUST leave the schedule in a state where a retried deletion can complete successfully.
- **FR-018**: System MUST emit a structured log entry for every deletion attempt, recording the schedule identifier, whether the schedule was removed or marked deleted, and the outcome including a reason when the attempt fails.
- **FR-019**: A soft-deleted schedule MUST report no upcoming execution time wherever an upcoming execution time is presented.

### Key Entities *(include if feature involves data)*

- **Schedule**: A user-owned scheduled task. This feature adds a deletion state attribute indicating whether the schedule has been soft-deleted. Relevant existing attributes: identifier, owner, name, upcoming execution time, creation and update timestamps.
- **Run**: A record of a single execution of a schedule, belonging to exactly one schedule. Its existence determines whether deleting the schedule removes the record or marks it deleted. Runs are never modified or removed by this feature.
- **Scheduling registration**: The scheduling engine's record that causes a schedule to execute at its configured times. Deleting a schedule always removes this registration, whether the schedule is removed or marked deleted.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can delete any schedule they own — with or without run history — in a single request, and in 100% of cases the schedule produces no further executions afterwards.
- **SC-002**: In 100% of deletions of schedules that have run history, every previously recorded run for that schedule is still retrievable after deletion.
- **SC-003**: In 100% of deletions of schedules with no run history, the schedule is absent from all list and details results afterwards.
- **SC-004**: The default schedules list contains zero soft-deleted schedules, and a deleted-only list contains zero live schedules, in 100% of requests.
- **SC-005**: 100% of attempts to update, pause, resume, or re-delete a soft-deleted schedule are rejected, and no soft-deleted schedule ever returns to a live or executing state.
- **SC-006**: 100% of deletion attempts that report success leave no schedule that is presented as live yet unable to execute, and no schedule that is presented as deleted yet still able to execute.
- **SC-007**: Deletion requests complete within the same responsiveness users already experience for schedule create and update operations, with no user-visible delay attributable to run-history size.
- **SC-008**: Every deletion attempt, successful or failed, is traceable from logs to the specific schedule and the branch taken (removed vs. marked deleted), in 100% of cases.

## Assumptions

- The existing owner-scoping rule applies unchanged: a user can only delete, list, or view their own schedules, and another user's schedule is indistinguishable from a non-existent one.
- The deletion operation is exposed on the existing single-schedule address as a delete-style request, consistent with the existing retrieve and update operations, and returns no content body on success.
- "No runs exist" means zero run records of any status — including in-progress, successful, and failed runs. Any run record at all, regardless of status or age, triggers the mark-as-deleted branch.
- A soft-deleted schedule remains individually retrievable by its owner so that the deletion state and the surviving run history stay discoverable; it is only excluded from the default list view.
- The deletion filter on the list is a two-valued choice (live or deleted) rather than an "all" option, since the request specifies the list is filtered by the deletion state and there is no stated need for a mixed view.
- Rejections of operations on soft-deleted schedules and of re-deletion are reported as the schedule not being available for that operation, using the same error style already used for schedules that cannot be found or acted upon.
- Deletion is permanent by design; no retention period, purge job, or administrative recovery path is introduced. Removing soft-deleted schedule records permanently at a later date is out of scope.
- No bulk or cascading deletion is introduced: exactly one schedule is deleted per request, and run records are never deleted by this feature.
- Existing consumers that read schedule list and details responses tolerate the addition of a new attribute; adding the deletion state is treated as a backward-compatible addition.
- The deletion state is derived and maintained by the system only; it is not part of any request body schema, and existing create/update request handling already disregards unrecognised body fields.

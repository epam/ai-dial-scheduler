# Feature Specification: Schedule Pause & Resume Endpoints

**Feature Branch**: `012-schedule-pause-resume`

**Created**: 2026-06-12

**Status**: Draft

**Input**: User description: "Implement schedule pause/resume endpoints that will pause and resume provided schedule (POST /schedules/{schedule_id}/pause, POST /schedules/{schedule_id}/resume). Only schedule creator may perform that action, non-creators should get 404 error, no schedule database changes needed, just APScheduler pause/resume call for job by its id."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pause one of my schedules (Priority: P1)

A schedule owner temporarily does not want one of their schedules to fire — for example
during a maintenance window or while investigating a problem — but does not want to delete
the schedule and lose its definition and history. The owner pauses the schedule; from that
point on it stops firing until they choose to resume it, while remaining fully intact.

**Why this priority**: Pausing is the primary new capability and delivers value on its own —
an owner can stop a schedule from firing without destroying it. Resume is only meaningful
once pause exists.

**Independent Test**: As the owner of an active schedule, pause it and confirm the request
succeeds and the schedule no longer has a scheduled next firing time, while the schedule
itself still exists and is still retrievable.

**Acceptance Scenarios**:

1. **Given** an active schedule owned by the requester, **When** the owner pauses it, **Then**
   the request succeeds and the schedule stops firing (it has no upcoming next run time).
2. **Given** a schedule the requester has just paused, **When** the owner retrieves or lists
   their schedules, **Then** the schedule is still present and reported as having no upcoming
   next run time.
3. **Given** a schedule that is already paused, **When** the owner pauses it again, **Then**
   the request still succeeds and the schedule remains paused (the action is idempotent).

---

### User Story 2 - Resume one of my schedules (Priority: P1)

After the reason for pausing has passed, the schedule owner resumes a previously paused
schedule so it begins firing again on its defined trigger, without having to recreate it.

**Why this priority**: Resume restores normal operation and is the natural counterpart to
pause; together they form the complete temporary-disable capability. It is equal in value to
pause and is the reason owners are willing to pause rather than delete.

**Independent Test**: As the owner of a paused schedule, resume it and confirm the request
succeeds and the schedule once again reports an upcoming next run time consistent with its
trigger.

**Acceptance Scenarios**:

1. **Given** a paused schedule owned by the requester, **When** the owner resumes it, **Then**
   the request succeeds and the schedule resumes firing on its defined trigger (it reports an
   upcoming next run time, when its trigger still yields future firings).
2. **Given** a schedule that is already active, **When** the owner resumes it, **Then** the
   request still succeeds and the schedule remains active (the action is idempotent).
3. **Given** a schedule the requester just resumed, **When** the owner retrieves it, **Then**
   it is reported with an upcoming next run time again.

---

### User Story 3 - Pause/resume are private to the schedule owner (Priority: P1)

Only the user who created a schedule may pause or resume it. A request from anyone else, or
for a schedule that does not exist, must be rejected as not found, and the response must not
reveal whether such a schedule exists or who owns it.

**Why this priority**: Allowing a non-owner to pause another user's schedule would let one
user disrupt another's automation, and even a distinct "forbidden" response would disclose
that the schedule exists. The same creator-scoped ownership rule already governs the other
schedule endpoints and must be honoured consistently here.

**Independent Test**: As user A, attempt to pause and to resume a schedule created by user B;
confirm both requests are rejected as not found, identically to acting on a schedule that
does not exist at all.

**Acceptance Scenarios**:

1. **Given** a schedule created by another user, **When** the requester tries to pause it,
   **Then** the request is rejected as not found and the schedule is not paused.
2. **Given** a schedule created by another user, **When** the requester tries to resume it,
   **Then** the request is rejected as not found and the schedule is not resumed.
3. **Given** a schedule identifier that does not exist, **When** the requester tries to pause
   or resume it, **Then** the request is rejected as not found, with the same response as the
   cross-owner case (no existence disclosure).

---

### Edge Cases

- **Unidentified caller**: A request with a missing or invalid credential is rejected as
  unauthorized before ownership is even considered, consistent with the other schedule
  endpoints.
- **Already in target state**: Pausing an already-paused schedule, or resuming an already-active
  schedule, succeeds without error and leaves the schedule in the requested state (idempotent).
- **Schedule exists but its underlying job no longer does**: A schedule whose trigger has
  already exhausted its firings (e.g. a one-off schedule that has run) may have no remaining
  scheduled job to pause or resume even though the owner still owns the schedule record. In
  that case the pause/resume request is reported as not found, consistent with the schedule
  having no active scheduling to act upon.
- **Malformed identifier**: A schedule identifier that is not a well-formed identifier is
  rejected without leaking internal detail, consistent with the existing schedule endpoints.
- **Resume of a schedule with no future firings**: Resuming a schedule whose trigger yields no
  future firings succeeds, but the schedule will report no upcoming next run time.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose an endpoint that pauses a single schedule, identified by
  that schedule's identifier, so the schedule stops firing until it is resumed.
- **FR-002**: The system MUST expose an endpoint that resumes a single previously paused
  schedule, identified by that schedule's identifier, so it begins firing again on its defined
  trigger.
- **FR-003**: Both endpoints MUST restrict the action to the user who created the schedule,
  determined the same way as the existing schedule endpoints (creator-scoped ownership derived
  from the authenticated caller).
- **FR-004**: When the named schedule does not exist, or exists but was created by a different
  user, both endpoints MUST respond as not found, using a response indistinguishable from the
  schedule genuinely not existing (no existence or ownership disclosure).
- **FR-005**: Pausing a schedule MUST stop it from firing while preserving the schedule's
  definition and history — no schedule record is deleted and no stored schedule data is
  altered by the action.
- **FR-006**: Resuming a schedule MUST restore its firing according to its existing defined
  trigger, without requiring the schedule to be redefined.
- **FR-007**: Both actions MUST be idempotent: pausing an already-paused schedule and resuming
  an already-active schedule MUST succeed and leave the schedule in the requested state.
- **FR-008**: Both endpoints MUST require an authenticated caller and MUST reject requests whose
  identity cannot be established as unauthorized, consistent with the other schedule endpoints.
- **FR-009**: The system MUST log each pause and resume action sufficiently to trace which
  caller acted on which schedule, without logging any secret, credential, or sensitive
  schedule payload.
- **FR-010**: After a successful pause, the schedule's reported upcoming next run time MUST
  reflect that it will not fire; after a successful resume, it MUST reflect the next firing
  derived from its trigger (or remain empty when the trigger yields no future firings).

### Key Entities *(include if feature involves data)*

- **Schedule**: The owning entity acted upon, already established by prior features. Provides
  the creator-based ownership used to authorize the action and the trigger that determines
  firing. Its stored definition is unchanged by pause/resume; only its active firing state is
  affected.
- **Caller Identity**: The authenticated requester's identity, resolved per request and used to
  enforce that only the schedule's creator may pause or resume it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A schedule owner can pause any active schedule they own, after which it produces
  no further firings until resumed, in 100% of cases.
- **SC-002**: A schedule owner can resume any schedule they own that they previously paused,
  after which it fires again according to its trigger, in 100% of cases.
- **SC-003**: 100% of pause and resume attempts against a schedule not owned by the requester
  (or that does not exist) are rejected as not found, indistinguishable from a non-existent
  schedule.
- **SC-004**: Pausing or resuming a schedule never deletes or alters the stored schedule
  definition — the schedule remains retrievable with its original definition before and after.
- **SC-005**: Repeating a pause on an already-paused schedule, or a resume on an already-active
  schedule, succeeds every time without error (idempotent).

## Assumptions

- **Ownership model reused**: "Owned by the requester" uses the same creator-based filter
  (`created_by` derived from the authenticated caller) that already governs the existing
  schedule read/update endpoints; no new authorization scheme is introduced.
- **Identifier is the job identifier**: A schedule's identifier is the same identifier used to
  locate its underlying scheduled job, so the action targets the job by that identifier
  directly. No new mapping or lookup table is introduced.
- **No persistence changes**: This feature introduces no new storage structures, columns, or
  migrations, and does not modify any stored schedule field. Pause/resume affects only the
  scheduling engine's view of whether the job fires.
- **Authentication mechanism reused**: Callers authenticate with the same credential mechanism
  already used by the existing schedule endpoints; identity resolution and unauthorized
  handling are unchanged.
- **Success response shape**: A successful pause or resume returns a success response for the
  affected schedule consistent with the existing schedule endpoints' conventions; no request
  body is required for either action.
- **Idempotent semantics**: Repeating an action that leaves the schedule in the same target
  state is treated as success rather than an error, to keep the operations safe to retry.
- **No bulk operations**: Each request pauses or resumes exactly one schedule identified in the
  path; pausing or resuming multiple schedules at once is out of scope.

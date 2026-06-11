# Feature Specification: Schedule Run List & Run Details Endpoints

**Feature Branch**: `010-schedule-run-endpoints`

**Created**: 2026-06-11

**Status**: Draft

**Input**: User description: "Add Schedule run list and Run details endpoints according to documentation/api_contract.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Browse the run history of a schedule (Priority: P1)

A schedule owner wants to see the execution history of one of their schedules — every
time the scheduled task fired, whether it succeeded, failed, is still running, or was
missed, and when each run started and ended. Because run histories can grow large, the
owner browses the history page by page.

**Why this priority**: This is the primary way an owner answers "is my schedule actually
working, and how often?". Without a browsable history the persisted run records (delivered
by the run-tracking feature) are invisible to users. It is the foundation the detail view
builds on.

**Independent Test**: As the owner of a schedule that has fired several times, request the
schedule's run list and confirm it returns the runs with their status and timing, a total
count, and page navigation links — and that requesting a second page returns the next set
of runs.

**Acceptance Scenarios**:

1. **Given** a schedule owned by the requester that has produced several runs, **When** the
   owner requests that schedule's run list, **Then** the response contains the total number
   of runs, the applied page size and starting position, and a page of runs where each run
   shows its identifier, status, start time, and end time (end time empty for runs that have
   not finished).
2. **Given** more runs exist than fit on one page, **When** the owner requests the run list,
   **Then** the response includes a link to the next page; **And** when not on the first
   page, a link to the previous page.
3. **Given** the requester is on the last page of runs, **When** they view the response,
   **Then** no next-page link is provided.
4. **Given** a schedule owned by the requester that has never run, **When** the owner
   requests its run list, **Then** the response reports a total count of zero and an empty
   result set rather than an error.

---

### User Story 2 - Inspect a single run in detail (Priority: P1)

After spotting a run of interest in the history (for example a failed one), the schedule
owner wants to open that single run to see its full outcome — its status, timing, the
result it produced (or the reason it failed), and the schedule definition that was in
effect when it ran.

**Why this priority**: Diagnosing "what happened and why" requires the per-run result and
the schedule definition at fire time, which the list view intentionally omits. This turns
the history from a status board into an actionable diagnostic tool. It is equal in user
value to the list and depends on it for discovery.

**Independent Test**: As the owner of a schedule, pick a known run identifier and request
that run's details; confirm the response includes the run's status, start and end times,
its result (or failure reason), and the schedule's display name, trigger, and properties as
they applied to that run.

**Acceptance Scenarios**:

1. **Given** a run belonging to a schedule owned by the requester, **When** the owner
   requests that run's details, **Then** the response includes the run identifier, status,
   start time, end time, the run's result, and the associated schedule's display name,
   trigger, and properties.
2. **Given** a run that completed successfully, **When** its details are requested, **Then**
   the result field holds the produced result and the end time is populated.
3. **Given** a run that failed, **When** its details are requested, **Then** the result field
   conveys the failure reason and the end time is populated.
4. **Given** a run that is still in progress, **When** its details are requested, **Then** the
   status reflects "in progress" and the end time is empty.

---

### User Story 3 - Runs are private to the schedule owner (Priority: P1)

A user must only ever see runs for schedules they themselves created. Run history and run
details for another user's schedule must not be reachable, and the response must not reveal
whether such a schedule or run exists.

**Why this priority**: Run results and schedule definitions can contain sensitive payloads
and configuration. Leaking them — or even confirming a schedule's existence — across users
is a privacy/security defect. The same ownership filter already governs the existing
schedule endpoints, so the run endpoints must honour it consistently.

**Independent Test**: As user A, attempt to read the run list and a specific run for a
schedule created by user B; confirm both requests are rejected as not found, identically to
requesting a schedule that does not exist at all.

**Acceptance Scenarios**:

1. **Given** a schedule created by another user, **When** the requester asks for that
   schedule's run list, **Then** the request is rejected as not found.
2. **Given** a run that belongs to another user's schedule, **When** the requester asks for
   that run's details, **Then** the request is rejected as not found.
3. **Given** a schedule identifier that does not exist, **When** the requester asks for its
   run list or a run within it, **Then** the request is rejected as not found, with the same
   response as the cross-owner case.

---

### Edge Cases

- **Run does not belong to the schedule in the path**: A run identifier that exists but
  belongs to a different schedule than the one named in the request path MUST be treated as
  not found — a run is only retrievable through its own schedule.
- **Unknown run identifier**: Requesting details for a run identifier that does not exist
  under an owned schedule returns not found.
- **Pagination beyond the end**: A starting position at or past the total count returns an
  empty result set with the correct total count and no next-page link, rather than an error.
- **Invalid pagination input**: A negative starting position or a non-positive / oversized
  page size is rejected (or clamped to allowed bounds) consistently with the existing
  schedule list endpoint.
- **Malformed identifiers**: A schedule or run identifier that is not a well-formed
  identifier is rejected as a bad request (or not found) without leaking internal detail.
- **Sensitive content**: Results and failure reasons surfaced in run details MUST NOT expose
  secrets or credentials.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose an endpoint to list the runs belonging to a single
  schedule, identified by that schedule's identifier.
- **FR-002**: The run list response MUST be paginated and MUST include the total number of
  matching runs, the applied page size, the applied starting position, the page of run
  results, and links to the next and previous pages (omitted when not applicable), matching
  the pagination shape already used by the schedule list endpoint.
- **FR-003**: Each run in the list MUST expose its identifier, its status (one of
  "in progress", "success", "error", or "missed"), its start time, and its end time (empty
  when the run has not finished).
- **FR-004**: The system MUST expose an endpoint to retrieve the full details of a single
  run, identified by both its schedule's identifier and the run's identifier.
- **FR-005**: The run details response MUST include the run identifier, status, start time,
  end time, the run's result, and the associated schedule's display name, trigger, and
  properties.
- **FR-006**: For a successful run, the result field MUST carry the produced result; for a
  failed run, it MUST carry the failure reason; for an in-progress run it MAY be empty.
- **FR-007**: Both endpoints MUST restrict results to schedules created by the requesting
  user, determined the same way as the existing schedule endpoints; runs of schedules owned
  by other users MUST NOT be returned.
- **FR-008**: When the named schedule does not exist or is not owned by the requester, both
  endpoints MUST respond as not found, using a response indistinguishable from the schedule
  genuinely not existing (no existence disclosure).
- **FR-009**: When the named run does not exist under the owned schedule, or exists but
  belongs to a different schedule than the one in the request path, the details endpoint MUST
  respond as not found.
- **FR-010**: The endpoints MUST validate pagination and identifier inputs and reject invalid
  input consistently with the existing schedule list/detail endpoints.
- **FR-011**: Both endpoints MUST be read-only and MUST NOT modify any run or schedule state.
- **FR-012**: The system MUST log access to run history and run details sufficient to trace
  who requested which schedule's runs, without logging any secret, credential, or full
  sensitive result payload.
- **FR-013**: Runs in the list MUST be returned in a stable, predictable order so that
  pagination across pages is consistent and does not skip or duplicate runs.

### Key Entities *(include if feature involves data)*

- **Schedule Run (list view)**: A summary of one execution attempt of a schedule — its
  identifier, status, start time, and end time. Belongs to exactly one schedule.
- **Schedule Run (detail view)**: The full view of one execution attempt — identifier,
  status, start time, end time, and the result it produced or the reason it failed, plus an
  embedded summary of the owning schedule (display name, trigger, properties) as it applied
  to that run.
- **Schedule**: The owning entity of runs, already established by prior features; provides
  the ownership (creator) used to authorize access and the display name, trigger, and
  properties embedded in run details.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A schedule owner can retrieve the complete run history of any schedule they own,
  paginated, with an accurate total count that equals the actual number of recorded runs.
- **SC-002**: Navigating page by page through a schedule's run history visits every run
  exactly once, with no run skipped or shown twice.
- **SC-003**: For 100% of runs, the detail view reports the correct status, start and end
  times, result-or-reason, and the schedule definition that applied to that run.
- **SC-004**: 100% of attempts to read runs of a schedule not owned by the requester are
  rejected as not found, and are indistinguishable from requests for a non-existent schedule.
- **SC-005**: Requesting the run list of a schedule that has never run returns an empty result
  with a zero total count and no error.
- **SC-006**: No run-listing or run-detail response exposes any secret, credential, or
  ownership information about other users' schedules.

## Assumptions

- **Run records already exist**: The persisted run records (status values "in progress",
  "success", "error", "missed", start/end times, result/reason, schedule snapshot) are
  produced by the existing run-tracking feature and are read as-is; this feature adds only the
  read endpoints over them.
- **Ownership model reused**: "Owned by the requester" uses the same creator-based filter
  (`created_by` derived from the authenticated user) that already governs the existing
  schedule read/update endpoints; no new authorization scheme is introduced.
- **Pagination conventions reused**: The run list adopts the same pagination contract,
  default page size, maximum page size, and `next`/`previous` link style already used by the
  schedule list endpoint, for consistency.
- **Default ordering**: Runs are listed most-recent-first (by start time, descending) unless
  the existing schedule list ordering convention dictates otherwise; this provides the stable
  ordering required by FR-013.
- **Failure reason carried in result**: A failed run's reason is surfaced through the same
  result field used for successful results, consistent with how run records persist outcomes.
- **No filtering/search in this increment**: Filtering runs by status or time range, and free
  text search, are out of scope; only straightforward pagination is provided.
- **No new persistence**: This feature introduces no new storage structures or migrations; it
  reads existing run and schedule data only.

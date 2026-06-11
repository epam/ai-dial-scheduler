# Feature Specification: Schedule Management Endpoints

**Feature Branch**: `007-schedule-endpoints`

**Created**: 2026-06-11

**Status**: Draft

**Input**: User description: "Implement Insert Schedule, Update Schedule, List Schedules and Schedule Details endpoints using documentation/api_contract.md and documentation/properties.md. Add validated trigger and properties models, an execute_scheduled_job task, and wire schedule persistence to the scheduler with rollback on failure."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Create a schedule (Priority: P1)

A user submits a schedule definition (display name, a single trigger, and task properties) and the system persists it and registers it for future automated execution. The user receives back the stored schedule including the computed next run time.

**Why this priority**: Creating schedules is the foundational capability of the product. Without it, none of the other operations have data to act on. It delivers the core value on its own.

**Independent Test**: Submit a valid create request with either a cron trigger or a date trigger, confirm a persisted schedule is returned with a generated identifier and a next run time, and confirm the schedule is registered for execution.

**Acceptance Scenarios**:

1. **Given** a valid request with a cron trigger and valid properties, **When** the user creates a schedule, **Then** a new schedule is stored with a generated identifier and the response includes the next run time.
2. **Given** a valid request with a date trigger, **When** the user creates a schedule, **Then** the schedule is stored and registered to run once at the specified date.
3. **Given** a request where both `cron` and `date` triggers are provided, **When** the user creates a schedule, **Then** the request is rejected with a validation error and nothing is persisted.
4. **Given** a request where neither `cron` nor `date` trigger is provided, **When** the user creates a schedule, **Then** the request is rejected with a validation error.
5. **Given** a valid stored schedule whose registration for execution fails, **When** the create operation runs, **Then** the persisted record is removed (rolled back) and the user receives an error.
6. **Given** properties whose fields violate the rules for the selected `target_type`, **When** the user creates a schedule, **Then** the request is rejected with a validation error and nothing is persisted.

---

### User Story 2 - Update a schedule (Priority: P2)

A user modifies an existing schedule they own by supplying a new full definition. The system updates the stored record and re-registers the schedule for execution, returning the updated schedule with its recomputed next run time.

**Why this priority**: Editing is the natural follow-on to creation and is required for real operational use, but the product is already minimally usable with create + read.

**Independent Test**: Update an existing schedule with a changed trigger or properties, confirm the stored record reflects the change and the next run time is recomputed.

**Acceptance Scenarios**:

1. **Given** an existing schedule owned by the user and a valid update request, **When** the user updates the schedule, **Then** the stored record is updated and re-registered, and the response includes the recomputed next run time.
2. **Given** an update request that fails validation (invalid trigger or properties), **When** the user updates the schedule, **Then** the request is rejected and the stored record is unchanged.
3. **Given** a valid update whose re-registration for execution fails, **When** the update operation runs, **Then** the stored record is restored to its prior state (rolled back) and the user receives an error.
4. **Given** a schedule identifier that does not exist or is not owned by the user, **When** the user updates it, **Then** the request is rejected with a not-found result.

---

### User Story 3 - List schedules (Priority: P2)

A user retrieves a paginated list of the schedules they own, seeing summary information for each including next run time and trigger type.

**Why this priority**: Listing lets users find and manage their schedules; valuable but dependent on creation existing first.

**Independent Test**: With several schedules stored, request the list with a limit and offset and confirm the correct page, total count, and navigation links are returned, scoped to the requesting user.

**Acceptance Scenarios**:

1. **Given** multiple schedules owned by the user, **When** the user lists schedules with a limit and offset, **Then** a page of summaries is returned with the total count and previous/next navigation links.
2. **Given** schedules owned by other users, **When** the user lists schedules, **Then** only the requesting user's schedules are included.
3. **Given** a page at the start or end of the result set, **When** the user lists schedules, **Then** the corresponding navigation link (previous or next) is omitted when there is no adjacent page.

---

### User Story 4 - Schedule details (Priority: P2)

A user retrieves the full details of a single schedule they own, including display name, trigger, properties, next run time, and audit timestamps.

**Why this priority**: Viewing a single schedule's full definition is needed to inspect and confirm configuration; complements listing.

**Independent Test**: Request an existing owned schedule by identifier and confirm the full detail payload is returned; request a non-owned or missing identifier and confirm a not-found result.

**Acceptance Scenarios**:

1. **Given** an existing schedule owned by the user, **When** the user requests its details, **Then** the full schedule including trigger, properties, next run time, and timestamps is returned.
2. **Given** a schedule identifier that does not exist or belongs to another user, **When** the user requests its details, **Then** a not-found result is returned.

---

### Edge Cases

- Both `cron` and `date` triggers supplied, or neither supplied → rejected as invalid (exactly one is required).
- `target_type` is `chat_completion` but properties contain fields that are not applicable (e.g., method) or omit fields required for that type → rejected per the target-type rules.
- A schedule is persisted but registration with the scheduler fails → the persisted change is rolled back so the database and the scheduler never diverge.
- A date trigger set in the past, or a cron expression that yields no future runs → next run time may be absent/null in the response.
- Pagination requested beyond the available range → an empty result page with the correct total count.
- Reading or updating a schedule owned by another user → treated as not found (ownership-scoped).

## Requirements *(mandatory)*

### Functional Requirements

#### Schedule creation

- **FR-001**: System MUST expose an endpoint to create a schedule accepting a display name, exactly one trigger, and task properties.
- **FR-002**: System MUST generate the schedule identifier as a UUID4 rendered as a string, and use the same value as both the stored schedule identifier and the scheduler job identifier.
- **FR-003**: System MUST persist the schedule record before registering the job with the scheduler.
- **FR-004**: System MUST register the job with the scheduler using the generated identifier, replacing any existing job with the same identifier, with the task properties passed as the job arguments and the schedule identifier added to those arguments at registration time.
- **FR-005**: System MUST select the scheduler trigger type (cron-based or single-date) based on the trigger provided in the request and configure it from the request values.
- **FR-006**: System MUST register the job to invoke the schedule-execution task.
- **FR-007**: System MUST roll back (remove) the persisted schedule record and surface an error if registering the job with the scheduler fails.
- **FR-008**: System MUST populate the next run time in the create response from the scheduler's job store, looked up by schedule identifier.

#### Schedule update

- **FR-009**: System MUST expose an endpoint to update an existing schedule identified by its identifier, accepting the same definition fields as creation.
- **FR-010**: System MUST update the stored schedule record before re-registering the job with the scheduler.
- **FR-011**: System MUST re-register the job using the schedule identifier, replacing the existing job, with task properties as job arguments plus the schedule identifier added at registration time, and the appropriate trigger configured from the request.
- **FR-012**: System MUST roll back the stored record to its prior state and surface an error if re-registering the job fails.
- **FR-013**: System MUST populate the next run time in the update response from the scheduler's job store, looked up by schedule identifier.

#### Listing and details

- **FR-014**: System MUST expose an endpoint to list schedules with pagination (limit and offset), returning the total count, the limit, the offset, a page of schedule summaries, and previous/next navigation links.
- **FR-015**: Each list summary MUST include the identifier, next run time, trigger type, owner, and audit timestamps (created and updated).
- **FR-016**: System MUST omit the previous link on the first page and the next link on the last page.
- **FR-017**: System MUST expose an endpoint to retrieve a single schedule's full details including identifier, display name, next run time, trigger, properties, owner, and audit timestamps.
- **FR-018**: System MUST return a not-found result when a requested schedule does not exist or is not owned by the requesting user.

#### Ownership and scoping

- **FR-019**: All read and update operations MUST be scoped to the owning user; only the creator may read or update a schedule.
- **FR-020**: For this iteration, the owner identity used for the stored owner field and for read/update filtering MUST be the fixed mock value `mock_user_id` (the create response owner field is populated from this value).

#### Validation — trigger

- **FR-021**: System MUST validate the trigger so that exactly one of `cron` or `date` is provided; zero or both MUST be rejected.
- **FR-022**: The cron trigger MUST accept the field set (year, month, day, week, day_of_week, hour, minute, second) plus optional start and end dates, each individually optional.
- **FR-023**: The date trigger MUST accept a single run date/time.

#### Validation — properties

- **FR-024**: System MUST validate properties as a structured object with fields: target type (one of chat_completion, rest, sse), method (one of POST, PUT, GET, or none), url, payload, extra headers, and api version.
- **FR-025**: System MUST apply target-type-specific rules: for chat completion the method is not used, the url must be a valid OpenAI-compatible deployment url, the payload's stream flag is extracted if present (defaulting to false), and api version applies only to chat completion; extra headers override any injected headers of the same name.
- **FR-026**: System MUST reject create and update requests whose properties fail target-type validation, persisting nothing.

#### Execution task

- **FR-027**: System MUST provide a schedule-execution task whose arguments correspond to the schedule properties, which for this iteration outputs the current date/time and its arguments (placeholder behavior, no external calls yet).

#### Architecture

- **FR-028**: All database reads and writes MUST go through the ORM via a repository; no raw SQL.
- **FR-029**: Business logic MUST live in a service layer; endpoints MUST contain no business logic.
- **FR-030**: Scope is limited to the four endpoints above (create, update, list, details); delete, pause/resume, and run endpoints are out of scope.

### Key Entities *(include if feature involves data)*

- **Schedule**: The queryable business metadata for one scheduled job. Attributes: identifier (string, equal to the scheduler job id), display name, owner, trigger definition, task properties (call arguments), and audit timestamps (created, updated).
- **Trigger (request model)**: A definition carrying exactly one of a cron specification (field set plus optional start/end dates) or a single date. Determines the trigger type reported in listings (cron or date).
- **Properties (request model)**: The task run configuration: target type, method, url, payload, extra headers, api version — validated per target type and used as the execution task's arguments.
- **Scheduler job store**: The engine-owned record of registered jobs; the source of truth for a schedule's next run time, looked up by schedule identifier.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can create a schedule with a valid trigger and properties and receive back the stored schedule with a non-empty identifier and its next run time in a single request.
- **SC-002**: 100% of requests supplying zero or two triggers, or properties that violate target-type rules, are rejected with a validation error and result in no persisted change.
- **SC-003**: Whenever scheduler registration fails during create or update, no orphaned or stale schedule record remains — the database state matches the pre-operation state in 100% of such cases.
- **SC-004**: For any created schedule, the next run time returned by create, update, and details responses matches the value held by the scheduler job store for that identifier.
- **SC-005**: Listing returns only the requesting user's schedules, with an accurate total count and correct presence/absence of previous and next navigation links across all pages.
- **SC-006**: A schedule that does not exist or is owned by another user returns a not-found result for both details and update in 100% of attempts.

## Assumptions

- Owner identity is hardcoded to `mock_user_id` for this iteration; integration with the DIAL `/v1/info` endpoint to derive the real creator email is deferred to a later feature.
- The schedules table and the scheduler job store already exist (delivered by the preceding database-tables feature); this feature consumes them.
- Default pagination values follow the API contract examples (e.g., limit 20) when not supplied by the caller.
- Next run time may legitimately be null when the scheduler computes no upcoming run (e.g., a past date trigger or an exhausted cron range).
- The execution task is a placeholder in this iteration and performs no outbound requests; real target execution is a future feature.
- Validation of trigger and properties is enforced at the API/request layer (structured models), not by the database.

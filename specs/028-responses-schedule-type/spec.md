# Feature Specification: Responses Schedule Type

**Feature Branch**: `028-responses-schedule-type`

**Created**: 2026-07-21

**Status**: Draft

**Input**: User description: "Add new schedule type \"responses\", same to chat completion but that uses responses api. See res.py for responses api usage example."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Schedule a recurring Responses-API call (Priority: P1)

A user who wants scheduled model interactions using the newer Responses-style interaction (a single input/instructions request rather than a chat-message array) creates a schedule of type "responses". On each trigger, the system calls the configured deployment using the Responses interaction and stores the model's response as the run result, just as chat completion schedules do today.

**Why this priority**: This is the core of the feature — without a schedule that can be created with the new type and produce a stored run result, nothing else matters. It delivers a complete, demonstrable slice on its own.

**Independent Test**: Create a schedule of type "responses" against a valid deployment, let a run trigger, and confirm the run completes successfully with the model's response stored as the run result.

**Acceptance Scenarios**:

1. **Given** a valid "responses" schedule definition, **When** the user creates the schedule, **Then** the schedule is accepted and stored with type "responses".
2. **Given** a "responses" schedule, **When** a scheduled run triggers, **Then** the configured deployment is invoked using the Responses interaction and the response is stored as the run result.
3. **Given** a "responses" schedule run that returns successfully, **When** the run finishes, **Then** the run is recorded as successful with the assembled response as its result.

---

### User Story 2 - Consistent behavior with chat completion schedules (Priority: P2)

A user familiar with chat completion schedules expects "responses" schedules to behave the same way for everything that is not specific to the interaction itself: triggering (cron/date), timeouts, retries, run tracking, pause/resume, metadata, and authorization on behalf of the run owner.

**Why this priority**: Reusing the established schedule behavior is what makes the new type immediately usable and predictable; it builds directly on the P1 slice.

**Independent Test**: Configure a "responses" schedule with a timeout, a retry policy, and metadata, then confirm those behave identically to an equivalent chat completion schedule (timeout enforced, retries applied on retryable errors, run history recorded, schedule can be paused and resumed).

**Acceptance Scenarios**:

1. **Given** a "responses" schedule with a timeout, **When** a run exceeds the timeout, **Then** the run fails on timeout exactly as a chat completion run would.
2. **Given** a "responses" schedule with a retry policy, **When** a run fails with a retryable error, **Then** the retry policy is applied identically to a chat completion schedule.
3. **Given** a "responses" schedule, **When** it is paused and later resumed, **Then** triggering stops and resumes exactly as for other schedule types.
4. **Given** a "responses" schedule, **When** runs execute, **Then** they use the run owner's on-behalf-of authorization, the same mechanism used by chat completion runs.

---

### User Story 3 - Streaming and non-streaming produce the same stored result (Priority: P3)

A user may configure a "responses" schedule to run in streaming or non-streaming mode. Regardless of transport, the stored run result should be a single assembled response so later inspection works identically for both.

**Why this priority**: Unifying the result shape simplifies downstream handling and inspection, but the feature is still valuable if only one mode is supported first. It is an enabling refinement.

**Independent Test**: Run one streaming and one non-streaming "responses" schedule and confirm both store an equivalent assembled response result.

**Acceptance Scenarios**:

1. **Given** a streaming "responses" run, **When** the run completes, **Then** the stored result is an assembled response equivalent in shape to a non-streaming response.
2. **Given** a non-streaming "responses" run, **When** the run completes, **Then** the stored result is a response of the same shape as the assembled streaming result.

---

### Edge Cases

- **Validation parity with chat completion**: A "responses" schedule follows the same target-specific validation rules as chat completion (no HTTP method; the interaction-version/protocol field required for the interaction; default non-streaming when unspecified). An HTTP method supplied on a "responses" schedule is rejected.
- **Invalid or unreachable deployment**: If the configured deployment rejects the request or is unreachable, the run fails and is recorded with the error status and status code, exactly as a chat completion run would.
- **Failed runs**: When a run does not complete successfully (error, missed, timeout), no result is stored beyond the failure record, consistent with existing schedule behavior.
- **Conversation creation option**: The create-conversation option remains restricted to chat completion schedules and is rejected for "responses" schedules unless conversation creation is explicitly extended to this type (see Out of Scope).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support a new schedule target type "responses" that users can select when creating or updating a schedule.
- **FR-002**: A "responses" schedule MUST invoke the configured deployment using the Responses interaction (a single input/instructions request), rather than the chat-message-array interaction used by chat completion.
- **FR-003**: The system MUST store the model's response from a "responses" run as the run result, in a single assembled form for both streaming and non-streaming runs.
- **FR-004**: A "responses" schedule MUST reuse the existing schedule behaviors that are not interaction-specific: cron/date triggering, timeout enforcement, retry policy, run tracking and history, pause/resume, and metadata.
- **FR-005**: A "responses" run MUST execute using the run owner's on-behalf-of authorization, the same mechanism used by chat completion runs.
- **FR-006**: The system MUST apply target-specific validation to "responses" schedules equivalent to chat completion: no HTTP method permitted, the interaction-version/protocol field required, and a default of non-streaming when the streaming flag is unspecified.
- **FR-007**: When a "responses" run fails (deployment error, unreachable, or timeout), the system MUST record the run as failed with the appropriate status and, where available, the status code — identical to chat completion failure handling.
- **FR-008**: The "responses" schedule type MUST be visible in schedule read responses (details and list) so users can distinguish it from other schedule types.
- **FR-009**: The system MUST reject a "responses" schedule that enables the create-conversation option, unless conversation creation is explicitly supported for this type.
- **FR-010**: Streaming diagnostics for "responses" runs MUST record event-type discriminators only, never the streamed content, consistent with existing streaming diagnostics policy.

### Key Entities *(include if feature involves data)*

- **Schedule**: A scheduled job definition. Gains "responses" as a valid target type alongside chat completion, REST, and SSE. Its target-specific properties follow the same validation model as chat completion.
- **Schedule Run**: A single execution of a schedule. For "responses" schedules it stores the assembled Responses-interaction result.
- **Deployment**: The run target invoked via the Responses interaction using the run owner's authorization.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can create, read, update, and delete a "responses" schedule with 100% parity to the existing schedule management flows.
- **SC-002**: 100% of successful "responses" runs store the model's assembled response as the run result.
- **SC-003**: Streaming and non-streaming "responses" runs produce results of the same shape, so downstream inspection is identical for both.
- **SC-004**: Trigger behavior, timeout, retry, pause/resume, run tracking, and metadata behave identically for "responses" and chat completion schedules in 100% of comparable scenarios.
- **SC-005**: Failed "responses" runs are recorded with the correct failure status and status code in 100% of failure cases, matching chat completion behavior.
- **SC-006**: Attempting to enable the create-conversation option on a "responses" schedule is rejected 100% of the time (unless the option is explicitly extended to this type).

## Assumptions

- The Responses interaction is the single-input/instructions request illustrated in `res.py`, distinct from the chat-message-array interaction used by chat completion; both target the same kind of deployment and use the same authorization.
- The assembled-result approach used for chat completion (a single final response object for both streaming and non-streaming) applies equally to the Responses interaction, so the stored result shape is unified across transports.
- All non-interaction-specific schedule behavior (triggering, timeout, retry, run tracking, pause/resume, metadata, authorization) is reused unchanged; only the interaction invoked at run time differs.
- The create-conversation option remains chat-completion-only for this feature; extending it to "responses" is out of scope unless separately requested.
- The interaction-version/protocol field required for chat completion is likewise required for "responses" (defaulting behavior for the streaming flag matches chat completion).

## Out of Scope

- Creating DIAL conversations from "responses" runs (conversation creation remains chat-completion-only for now).
- Any changes to the chat completion, REST, or SSE schedule types beyond adding the new type alongside them.
- New trigger types, retry semantics, or run-tracking changes — these are reused as-is.

## Dependencies

- Requires access to the target deployment's Responses interaction, invoked with the run owner's on-behalf-of authorization (the same authorization used for chat completion runs).
- Reuses the existing schedule creation/validation, triggering, run-tracking, timeout, and retry mechanisms.

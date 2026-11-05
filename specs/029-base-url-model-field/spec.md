# Feature Specification: Base URL and Payload Model for Chat Completion

**Feature Branch**: `029-base-url-model-field`

**Created**: 2026-07-22

**Status**: Draft

**Input**: User description: "Use base url for chat completion and responses api. Get deployment name for chat completion not from url but from payload model field."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Configure chat completion schedule with a base URL and model (Priority: P1)

A user who schedules a recurring chat-completion task provides a base target URL (without a
deployment path segment) and specifies which model/deployment to call inside the request payload's
`model` field. When the schedule runs, the task calls the intended deployment and stores the
result, exactly as it would today, but the deployment is now driven by the payload rather than the
URL.

**Why this priority**: This is the core of the feature. It aligns chat completion with the way the
responses target type already works, giving a single, consistent way to point a scheduled task at a
model. Without it, the feature delivers nothing.

**Independent Test**: Create a chat-completion schedule with a base URL and a `model` value in the
payload, trigger a run, and confirm the run calls the model named in the payload and produces the
same result shape as before.

**Acceptance Scenarios**:

1. **Given** a chat-completion schedule whose URL is a base URL and whose payload contains a valid
   `model`, **When** the schedule runs, **Then** the task calls the deployment named in `payload.model`
   and completes successfully with the standard stored result.
2. **Given** a chat-completion schedule whose payload omits `model`, **When** the schedule is created
   or validated, **Then** the request is rejected with a clear validation error before any run occurs.
3. **Given** a chat-completion schedule configured as above with `stream` set to true, **When** the
   schedule runs, **Then** the streamed response is assembled into the same result shape as a
   non-streamed run.

---

### User Story 2 - Consistent behavior between chat completion and responses (Priority: P2)

A user who works with both chat-completion and responses schedules can rely on the same mental
model for both: provide a base URL, put the model in the payload. Documentation and validation
rules match between the two target types.

**Why this priority**: Consistency reduces user error and support load, but the essential capability
is already delivered by User Story 1. This story is about parity and clarity.

**Independent Test**: Compare the required configuration fields for a chat-completion schedule and a
responses schedule and confirm the URL and model conventions are identical.

**Acceptance Scenarios**:

1. **Given** the documentation for target properties, **When** a user reads the URL and model rules
   for chat completion and for responses, **Then** both describe a base URL plus a required
   `payload.model`.
2. **Given** a responses schedule, **When** it runs, **Then** its existing base-URL behavior is
   unchanged by this feature.

---

### User Story 3 - Conversation creation uses the payload model (Priority: P3)

A user who enables conversation creation on a chat-completion schedule still gets a saved
conversation attributed to the correct model, where the model is now taken from the payload rather
than parsed from the URL.

**Why this priority**: Conversation creation is an optional add-on used by a subset of chat-completion
schedules. It must keep working, but it depends on the core change in User Story 1.

**Independent Test**: Enable conversation creation on a chat-completion schedule using the new
configuration, run it, and confirm the saved conversation references the model from `payload.model`.

**Acceptance Scenarios**:

1. **Given** a chat-completion schedule with conversation creation enabled and a `model` in the
   payload, **When** the run succeeds, **Then** the saved conversation is associated with the model
   from `payload.model`.
2. **Given** the same schedule where the model refers to an application-style deployment, **When**
   the conversation is created, **Then** the correct deployment/application reference is used.

---

### Edge Cases

- What happens to **existing** chat-completion schedules that were created with a deployment path in
  the URL and no `model` in the payload? (See Assumptions — these must be migrated to the new format;
  they will fail validation otherwise.)
- What happens when a chat-completion URL still contains a `/deployments/{name}/...` path segment
  but the payload also specifies a `model`? The payload `model` is authoritative for selecting the
  deployment.
- How does the system handle a payload `model` value that names a nonexistent deployment? The run
  fails with the error returned by the target service, following existing run-failure handling.
- What happens when both `model` is missing and the URL carries no deployment information? Validation
  rejects the schedule.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Chat-completion tasks MUST use the schedule's configured URL as a base URL, without
  requiring or extracting a deployment name from the URL path.
- **FR-002**: Chat-completion tasks MUST determine the target deployment/model from the payload's
  `model` field.
- **FR-003**: The system MUST require a non-empty `model` value in the payload for chat-completion
  schedules and MUST reject creation or validation of a chat-completion schedule that lacks it, with
  a clear error.
- **FR-004**: Responses tasks MUST continue to use a base URL and the payload `model` field with no
  change in behavior.
- **FR-005**: Chat-completion runs MUST produce the same stored result shape as before this change,
  for both streaming and non-streaming payloads.
- **FR-006**: Conversation creation for chat-completion schedules MUST derive the model/deployment
  reference from the payload `model` field instead of from the URL, including correct handling of
  application-style deployment references.
- **FR-007**: User-facing documentation for target properties MUST describe the base-URL and
  required `payload.model` convention consistently for both chat completion and responses.
- **FR-008**: The per-user authentication behavior, streaming handling, and non-fatal
  conversation-creation failure behavior MUST remain unchanged.

### Key Entities *(include if data involved)*

- **Schedule target configuration**: The set of properties describing how a scheduled task calls its
  target — including target type, URL, payload, API version, and the conversation-creation flag. The
  relevant attributes for this feature are the URL (now a base URL for chat completion) and the
  payload's `model` value (now required for chat completion).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of chat-completion schedules configured with a base URL and a payload `model`
  successfully invoke the model named in the payload.
- **SC-002**: 100% of chat-completion schedules that omit a payload `model` are rejected at
  creation/validation time, before any run is attempted.
- **SC-003**: Chat-completion and responses target types share an identical URL-and-model
  configuration convention, verifiable from documentation and validation rules.
- **SC-004**: Existing successful run result shapes are unchanged, confirmed for both streaming and
  non-streaming chat-completion runs.
- **SC-005**: Conversation creation continues to succeed and attributes conversations to the model
  from the payload for all chat-completion schedules that enable it.

## Assumptions

- Existing chat-completion schedules that rely on a deployment path embedded in the URL and have no
  `model` in the payload will be migrated to the new format as part of adopting this change; the
  system is not required to accept the legacy deployment-in-URL format after this change. (This is a
  breaking change to the chat-completion configuration contract.)
- The payload `model` value uses the same deployment/application naming that the target service
  expects, matching the convention already used by the responses target type.
- No new configuration setting or global base URL is introduced; the base URL continues to come from
  each schedule's own target URL.
- The change applies to the chat-completion target type only; responses, REST, and SSE target types
  are out of scope except for the documentation-consistency requirement.

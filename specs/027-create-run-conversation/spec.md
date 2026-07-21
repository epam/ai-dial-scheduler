# Feature Specification: Create DIAL Conversation on Run Complete

**Feature Branch**: `027-create-run-conversation`

**Created**: 2026-07-17

**Status**: Draft

**Input**: User description: "For chat completion schedules there should be an option to create DIAL conversation on run complete."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Opt in to conversation creation for a chat completion schedule (Priority: P1)

A user creating (or updating) a chat completion schedule wants the result of each run to appear in DIAL Chat, just as if they had run the prompt themselves. They set a flag on the schedule so that every completed run produces a browsable DIAL conversation containing their prompt and the model's answer.

**Why this priority**: This is the core value of the feature — without the opt-in flag and the resulting conversation, nothing else matters. It delivers a complete, demonstrable slice on its own.

**Independent Test**: Create a chat completion schedule with the conversation option enabled, let a run complete successfully, and confirm a DIAL conversation exists in the owner's DIAL storage containing the user prompt and the assistant response.

**Acceptance Scenarios**:

1. **Given** a chat completion schedule with the conversation option enabled, **When** a run completes successfully, **Then** a DIAL conversation is created in the run owner's DIAL storage containing two messages — the user prompt from the run and the assistant response from the run result.
2. **Given** a chat completion schedule with the conversation option disabled (the default), **When** a run completes successfully, **Then** no DIAL conversation is created.
3. **Given** a user creating a schedule, **When** they do not specify the conversation option, **Then** the schedule is created with conversation creation disabled.

---

### User Story 2 - Correct model attribution for model and application deployments (Priority: P2)

A user schedules runs against either a plain model deployment or an application deployment. When the conversation is created, the model shown in DIAL Chat must be the correct underlying model identifier so the conversation renders and behaves the same way a manually created one would.

**Why this priority**: Without correct model attribution, application-backed conversations render incorrectly or cannot be reopened in DIAL Chat. It is essential for usefulness but builds on the P1 slice.

**Independent Test**: Run one schedule against a model deployment and one against an application deployment, both with the conversation option enabled, and confirm each produced conversation carries the correct model identifier (the deployment id for models; the application's underlying reference for applications).

**Acceptance Scenarios**:

1. **Given** a schedule targeting a plain model deployment, **When** the conversation is created, **Then** the conversation's model identifier is the deployment id used for the run.
2. **Given** a schedule targeting an application deployment, **When** the conversation is created, **Then** the system resolves the application's underlying reference and uses that reference as the conversation's model identifier.
3. **Given** an application deployment whose reference cannot be resolved, **When** the conversation is created, **Then** the failure is handled per the conversation-failure policy (see Edge Cases) and the run outcome itself is unaffected.

---

### User Story 3 - Conversation reflects full assistant response including custom content (Priority: P2)

A user whose schedule targets a deployment that returns rich responses (tool execution history, usage-statistics stages, and other custom content) wants the created conversation to preserve that content, so the DIAL Chat view matches what an interactive session would show.

**Why this priority**: Fidelity of the stored conversation determines whether it is genuinely equivalent to a chat session. Important, but the feature is still valuable with plain-text responses only.

**Independent Test**: Run a schedule against a deployment that returns custom content (e.g., stages / tool execution history) with the conversation option enabled, and confirm the assistant message in the created conversation includes both the textual content and the custom content.

**Acceptance Scenarios**:

1. **Given** a completed run whose result contains assistant content and custom content, **When** the conversation is created, **Then** the assistant message includes both the content and the custom content.
2. **Given** a completed run whose result contains only plain text content, **When** the conversation is created, **Then** the assistant message contains that text and no custom content.

---

### User Story 4 - Consistent result storage for streaming and non-streaming runs (Priority: P3)

A user may schedule streaming or non-streaming chat completions. Regardless of transport, the stored run result should be a single assembled completion so that conversation creation (and any later inspection of the result) works identically for both.

**Why this priority**: Unifying result shape simplifies conversation creation and improves result consistency, but conversations can still be created without it if the two shapes are handled separately. It is an enabling refinement.

**Independent Test**: Run one streaming and one non-streaming chat completion schedule and confirm both store an equivalent assembled completion result and both produce equivalent conversations.

**Acceptance Scenarios**:

1. **Given** a streaming chat completion run, **When** the run completes, **Then** the stored result is an assembled completion response equivalent in shape to a non-streaming completion.
2. **Given** a non-streaming chat completion run, **When** the run completes, **Then** the stored result is a completion response of the same shape as the assembled streaming result.

---

### Edge Cases

- **Non-chat-completion schedules**: The conversation option MUST be rejected (or ignored per FR-002) for schedules that are not chat completion type. A user attempting to enable it on a REST or SSE schedule receives a validation error.
- **Failed or non-successful runs**: No conversation is created when a run does not complete successfully (error, missed, or in-progress).
- **Conversation creation failure**: If the run itself succeeds but the subsequent conversation creation fails (e.g., storage unavailable, bucket unresolved, application reference unresolvable), the run remains successful; the conversation-creation failure is logged for diagnostics only (not surfaced on the run record) and does not overwrite or invalidate the run result.
- **Missing assistant content**: If a successful run result has no assistant message content, the conversation is still created with whatever content and custom content are available (possibly empty content).
- **Idempotency / re-runs**: Each run produces its own conversation stored under a run-specific location, so repeated runs of the same schedule do not overwrite each other.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow a schedule to carry a boolean option indicating whether a DIAL conversation should be created when a run completes. The option MUST default to disabled (false).
- **FR-002**: The system MUST only permit the conversation option to be enabled for chat completion schedules; enabling it for non-chat-completion schedule types MUST be rejected at schedule creation/update time.
- **FR-003**: The system MUST create a DIAL conversation only for runs of schedules that have the option enabled and only when the run completes successfully.
- **FR-004**: The created conversation MUST contain exactly two messages: a user message sourced from the run's snapshot (the scheduled user prompt and associated request context such as deployment), and an assistant message sourced from the run's stored result.
- **FR-005**: The assistant message MUST include the response content and any custom content present in the run result (e.g., tool execution history, stages).
- **FR-006**: For schedules targeting a plain model deployment, the conversation's model identifier MUST be the deployment id used for the run.
- **FR-007**: For schedules targeting an application deployment, the system MUST resolve the application's underlying reference (via the DIAL application details) and use that reference as the conversation's model identifier.
- **FR-008**: The system MUST distinguish application deployments from model deployments in order to apply the correct model-attribution rule (FR-006 vs FR-007).
- **FR-009**: The system MUST store each created conversation in the run owner's DIAL storage following the convention `conversations/{bucket}/.scheduler/{schedule_id}/{run_id}`.
- **FR-010**: The system MUST resolve the owner's DIAL storage bucket needed to construct the conversation storage location.
- **FR-011**: The system MUST use the run owner's credentials/authorization when reading application details, resolving the bucket, and writing the conversation, so that stored resources belong to the run owner.
- **FR-012**: The system MUST store a single assembled completion response as the run result for chat completion runs, using the same result shape for both streaming and non-streaming runs.
- **FR-013**: A conversation-creation failure MUST NOT change a successful run's success outcome or its stored result. Such failures MUST be logged for diagnostics only and MUST NOT be surfaced on the run record.
- **FR-014**: The conversation option MUST be visible in schedule read responses so users can see whether it is enabled.
- **FR-015**: The system MUST NOT log or expose the prompt content, response content, or credentials as part of conversation-creation diagnostics beyond what is strictly necessary; streaming diagnostics MUST record event type discriminators only, not streamed content.

### Key Entities *(include if feature involves data)*

- **Schedule**: A scheduled job definition. Gains a boolean conversation-creation option that is only valid for chat completion schedules and defaults to disabled.
- **Schedule Run**: A single execution of a schedule. Provides the point-in-time snapshot (user prompt, deployment/target, request settings) used as the conversation's user message, and stores the assembled completion result used as the conversation's assistant message.
- **DIAL Conversation**: A DIAL Chat resource created after a successful run, consisting of a user message and an assistant message, attributed to a model identifier, and stored at a run-specific location in the owner's DIAL storage.
- **Deployment (model vs application)**: The run target. Models are attributed by their deployment id; applications are attributed by their resolved underlying reference.
- **DIAL Storage Bucket**: The owner-scoped storage namespace under which the conversation is written.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a chat completion schedule with the option enabled, 100% of successful runs result in a corresponding DIAL conversation stored at the run-specific location.
- **SC-002**: For schedules with the option disabled (the default), 0% of runs create a conversation.
- **SC-003**: Conversations created from application-deployment runs carry the correct underlying model reference in 100% of cases, so they open and render in DIAL Chat identically to a manually created conversation.
- **SC-004**: Attempting to enable the option on a non-chat-completion schedule is rejected 100% of the time with a clear validation error.
- **SC-005**: A conversation-creation failure never causes a genuinely successful run to be reported as failed (0% false failures attributable to conversation creation).
- **SC-006**: Streaming and non-streaming chat completion runs produce results of the same shape, and conversations created from either are structurally equivalent.

## Assumptions

- The conversation-creation option is carried as part of the schedule's chat-completion properties (which are captured in the run snapshot), so no separate storage change is required to make it available to the run executor; validation restricting it to chat completion schedules lives with the schedule properties. (Implementation detail; documented here as the reasonable default.)
- The run owner's DIAL credentials already available to the run (the same on-behalf-of authorization used to execute the completion) are sufficient to resolve the bucket, read application details, and write the conversation. No new credential type is introduced.
- Conversation creation happens after the run result is persisted as a distinct, best-effort step; its failure is isolated from the run's success/failure determination (see FR-013 and the open clarification on surfacing).
- The DIAL application details response exposes an underlying model reference field usable as the conversation model identifier (verified against the live DIAL Core API for the example application deployment).
- Each run stores its conversation at a distinct `{run_id}` path, so no overwrite/idempotency conflict arises between runs of the same schedule.
- Existing example conversations and completion responses under `conversations/app` and `conversations/gpt-5-6` are representative of the target conversation and result shapes for applications and models respectively.

## Dependencies

- Requires access to the DIAL Core API for: resolving the owner's storage bucket, reading application deployment details (to obtain the reference), and writing the conversation resource.
- Reuses the existing run-owner authorization mechanism used to execute chat completion runs.

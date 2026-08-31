# Feature Specification: Persist Conversation ID for Schedule Runs

**Feature Branch**: `036-persist-run-conversation-id`
**Created**: 2026-08-26
**Status**: Draft
**Input**: User description: "Persist conversation ID for schedule runs. Persist the generated DIAL conversation ID/path for each schedule run and return it in the runs list response. Acceptance criteria: Save the conversation ID after successful conversation creation. Return it when listing schedule runs. Return null when no conversation was created."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See the conversation for a run in the runs list (Priority: P1)

A user who has enabled conversation creation on a schedule browses the list of runs for that schedule and wants each run to show the identifier of the DIAL conversation it produced, so they can locate and open the exact conversation that corresponds to that run without guessing or searching by name.

**Why this priority**: This is the whole point of the feature. Today the conversation is created and stored in DIAL, but its identifier is discarded — a user has no reliable way to link a specific run to its conversation from the runs list. Delivering the persisted, returned identifier is a complete, demonstrable slice on its own.

**Independent Test**: On a schedule with conversation creation enabled, let a run complete successfully so a conversation is created, then list that schedule's runs and confirm the run entry carries the identifier of the created conversation.

**Acceptance Scenarios**:

1. **Given** a schedule with conversation creation enabled, **When** a run completes successfully and its DIAL conversation is created, **Then** the run record retains the created conversation's identifier.
2. **Given** a run whose conversation was created, **When** the runs of its schedule are listed, **Then** that run's entry includes the conversation identifier.
3. **Given** a run whose conversation identifier is persisted, **When** the run is listed again at any later time, **Then** the same identifier is returned unchanged.

---

### User Story 2 - Distinguish runs that produced no conversation (Priority: P1)

A user viewing the runs list also sees runs that did not create a conversation — because the schedule had conversation creation disabled, because the run did not complete successfully, or because conversation creation was attempted but did not complete. Those runs must clearly indicate the absence of a conversation rather than an empty or misleading value.

**Why this priority**: Correctly representing "no conversation" is inseparable from the core value: a consumer of the runs list must be able to tell, without ambiguity, whether a conversation exists for each run. It is a small, independently verifiable addition to Story 1.

**Independent Test**: List runs for a schedule that includes a run with conversation creation disabled and a run that failed, and confirm each such run's conversation identifier is reported as absent (null), while a run that did create a conversation reports its identifier.

**Acceptance Scenarios**:

1. **Given** a run of a schedule with conversation creation disabled, **When** the runs are listed, **Then** that run's conversation identifier is reported as absent (null).
2. **Given** a run that did not complete successfully, **When** the runs are listed, **Then** its conversation identifier is reported as absent (null).
3. **Given** a run that completed successfully but whose conversation creation did not complete, **When** the runs are listed, **Then** its conversation identifier is reported as absent (null).

---

### Edge Cases

- **In-progress run**: A run that has not finished has no conversation yet; its conversation identifier is reported as absent (null).
- **Conversation creation failure after a successful run**: Consistent with the existing best-effort policy, a failed conversation creation MUST NOT change the run's success outcome; the conversation identifier is simply left absent (null) for that run.
- **Historical runs**: Runs recorded before this feature existed have no persisted conversation identifier; they are reported as absent (null).
- **Non-chat-completion schedules**: Schedule types that never create conversations always report the conversation identifier as absent (null).
- **Re-runs of the same schedule**: Each run carries its own conversation identifier; one run's identifier never overwrites another's.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST persist the created DIAL conversation's identifier on the corresponding run record after a conversation is successfully created for that run.
- **FR-002**: The persisted conversation identifier MUST be the identifier/path that uniquely addresses the created conversation in DIAL storage, so it can be used to locate and open that specific conversation.
- **FR-003**: The system MUST persist the conversation identifier only after conversation creation has succeeded; it MUST NOT persist a placeholder or partial identifier for conversations that were not created.
- **FR-004**: The system MUST include the persisted conversation identifier in the response returned when listing a schedule's runs.
- **FR-005**: The system MUST report the conversation identifier as absent (null) for any run that has no persisted conversation identifier — including runs of schedules with conversation creation disabled, runs that did not complete successfully, in-progress runs, runs whose conversation creation did not complete, and runs recorded before this capability existed.
- **FR-006**: Persisting the conversation identifier MUST NOT alter a run's success/failure outcome or its stored result; a failure to persist the identifier MUST be handled as a best-effort, non-fatal step consistent with the existing conversation-creation policy.
- **FR-007**: Once persisted, a run's conversation identifier MUST remain stable and MUST be returned unchanged on subsequent listings.
- **FR-008**: Each run's conversation identifier MUST be independent of other runs' identifiers, so that repeated runs of the same schedule each report their own conversation.

### Key Entities *(include if feature involves data)*

- **Schedule Run**: A single recorded execution of a schedule. Gains an optional conversation identifier that is populated only when a DIAL conversation was successfully created for that run, and is otherwise absent (null).
- **DIAL Conversation Identifier**: The unique identifier/path addressing the conversation created for a run in the run owner's DIAL storage. Produced by the existing conversation-creation flow; this feature retains it on the run and exposes it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every successful run that produces a conversation, the runs list returns a non-empty conversation identifier for that run in 100% of cases.
- **SC-002**: For every run that produced no conversation (disabled option, unsuccessful run, in-progress run, or failed conversation creation), the runs list reports the conversation identifier as absent (null) in 100% of cases.
- **SC-003**: A user can navigate from a listed run to the exact DIAL conversation it produced using only the returned identifier, without inspecting logs or searching by name.
- **SC-004**: Persisting or failing to persist the conversation identifier changes the reported success/failure outcome of 0% of runs.
- **SC-005**: A run's returned conversation identifier is identical across repeated listings (0% drift) once persisted.

## Assumptions

- The conversation-creation flow already generates a unique identifier/path for each created conversation; this feature captures and retains that identifier rather than generating a new one.
- The identifier persisted and returned is the addressable conversation identifier/path in DIAL storage, which is sufficient for a consumer to locate and open the conversation. A separately formatted shareable link is out of scope.
- The conversation identifier is added to the runs list response; whether the single-run detail response also carries it is a natural, backward-compatible extension and may be included during planning, but the mandated surface is the runs list.
- "Return null when no conversation was created" is satisfied by an absent/null value for the identifier field; no distinct reason codes are required to differentiate why a conversation is absent.
- Adding an optional, nullable conversation identifier is backward compatible: existing runs and existing consumers continue to work, with the identifier reported as absent for runs that never had one.

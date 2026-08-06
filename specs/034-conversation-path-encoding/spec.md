# Feature Specification: Conversation Path Encoding for Schedule Executions

**Feature Branch**: `034-conversation-path-encoding`

**Created**: 2026-08-06

**Status**: Draft

**Input**: User description: "When creating conversation after schedule execution for conversation path use 1) url encoded deployment id in both cases of model or application do not use reference id in path 2) Instead of slugification of schedule name in conversation path use url encoded value"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Conversations addressed by the deployment the user actually scheduled (Priority: P1)

As a user who scheduled a task against an application, when the schedule executes and a conversation is created, I need the conversation's location to identify the application I selected — not the underlying model it happens to be built on — so I can find and recognise the conversation by the thing I actually configured.

**Why this priority**: This is the core defect. Today an application-backed schedule produces a conversation filed under the application's underlying model, so users cannot tell which application produced it, and two different applications sharing a backing model become indistinguishable.

**Independent Test**: Configure a schedule against an application, trigger an execution, and confirm the stored conversation's location identifies the scheduled application and contains no trace of the application's underlying model reference.

**Acceptance Scenarios**:

1. **Given** a schedule targeting an application, **When** the schedule executes and a conversation is created, **Then** the conversation's location identifies that application's deployment id and does not contain the application's underlying model reference.
2. **Given** two schedules targeting two different applications that share the same underlying model, **When** both execute, **Then** their conversations are stored at distinct, individually identifiable locations.
3. **Given** a schedule targeting a model, **When** it executes and a conversation is created, **Then** the conversation's location identifies that model's deployment id — using the same rule applied to applications.

---

### User Story 2 - Deployment ids with reserved characters stored safely (Priority: P1)

As a user, I need conversations to be created successfully even when the scheduled deployment's id contains characters that carry structural meaning in a storage location (most notably the separators inside an application deployment id), so that executions never fail or land in an unintended location.

**Why this priority**: Application deployment ids contain path separators by construction. Placing such an id into a location without encoding it would silently split it across levels or produce an invalid location — an outright failure of User Story 1.

**Independent Test**: Configure schedules whose deployment ids contain separators and other reserved characters, execute them, and confirm each conversation is created at a single well-formed location whose deployment portion decodes back to the exact original id.

**Acceptance Scenarios**:

1. **Given** a deployment id containing path separators, **When** a conversation is created after execution, **Then** the separators are encoded so the deployment id occupies exactly one location segment rather than being split across several.
2. **Given** a deployment id containing reserved or non-ASCII characters, **When** a conversation is created, **Then** decoding the deployment portion of the location yields the exact original id, character for character.

---

### User Story 3 - Schedule names preserved exactly, not flattened (Priority: P2)

As a user, I want the conversation's location to carry my schedule's name exactly as I typed it — case, spacing, punctuation, and non-Latin characters intact — rather than a flattened, lossy version, so I can recognise precisely which schedule produced a conversation.

**Why this priority**: Flattening loses information and can collapse distinct schedule names into the same value, making conversations from different schedules hard to tell apart. It degrades recognisability rather than blocking the feature, so it ranks below the P1 stories.

**Independent Test**: Create schedules named with mixed case, spaces, punctuation, and non-Latin characters; execute each; and confirm that decoding the name portion of each conversation location reproduces the original schedule name exactly.

**Acceptance Scenarios**:

1. **Given** a schedule named "Daily Report Sync", **When** it executes and a conversation is created, **Then** the name portion of the location decodes to exactly "Daily Report Sync" — original capitalisation and spaces preserved, no substitution of spaces by other characters.
2. **Given** two schedules named "Weekly Sync" and "weekly-sync", **When** both execute, **Then** their conversation locations remain distinguishable from one another.
3. **Given** a schedule whose name is written entirely in a non-Latin script, **When** it executes, **Then** the name portion decodes back to the original name rather than being reduced or emptied.
4. **Given** a schedule name containing characters with structural meaning in a location, **When** it executes, **Then** those characters are encoded so the name occupies exactly one location segment.

---

### Edge Cases

- **Very long names or ids**: Encoding expands the stored length of special characters. A schedule name or deployment id close to the storage system's per-segment or total length limit may exceed it once encoded. The system must produce a location the storage system accepts, or fail conversation creation in a way that is logged and does not fail the schedule run itself.
- **Empty or whitespace-only schedule name**: The location must still be well-formed and must not collapse adjacent separators in a way that makes the segments ambiguous to read back.
- **Name containing the segment separator**: A schedule name that itself contains the character sequence used to separate parts of the final segment must not make the segments ambiguous.
- **Renaming a schedule between executions**: Each execution's conversation reflects the schedule name in effect at that execution's time; earlier conversations are not moved or renamed.
- **Existing conversations**: Conversations created before this change keep their existing locations; nothing is migrated or relocated.
- **Repeated execution of the same schedule**: Each run must still produce a distinct conversation location.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When creating a conversation after a schedule execution, the system MUST identify the deployment in the conversation's storage location using the deployment id configured on the schedule.
- **FR-002**: FR-001 MUST apply identically whether the schedule targets a model or an application — one rule, no branching by deployment type.
- **FR-003**: The system MUST NOT substitute an application's underlying model reference for the application's own deployment id anywhere in the conversation's storage location.
- **FR-004**: The system MUST URL-encode the deployment id before placing it in the conversation's storage location, such that reserved characters — including path separators — and non-ASCII characters are percent-encoded and the id occupies exactly one location segment.
- **FR-005**: When creating a conversation after a schedule execution, the system MUST carry the schedule's name into the conversation's storage location URL-encoded, preserving the name exactly as configured.
- **FR-006**: The system MUST NOT apply slugification — lowercasing, whitespace-to-hyphen substitution, transliteration, or stripping of characters — to the schedule name when constructing the conversation's storage location.
- **FR-007**: URL-decoding the deployment-id portion and the schedule-name portion of a conversation's storage location MUST yield the exact original deployment id and schedule name respectively.
- **FR-008**: The parts of the conversation's storage location MUST remain unambiguously separable after encoding, so the deployment id, schedule name, and run identifier can each be read back individually.
- **FR-009**: The conversation's display name MUST remain byte-identical to the final segment of its storage location — that is, it MUST carry the same URL-encoded deployment id and URL-encoded schedule name. Display name and final location segment MUST NOT diverge.
- **FR-010**: For an application-backed schedule, the deployment recorded inside the conversation document — the one the chat interface reopens the conversation against — MUST remain the application's underlying model reference, unchanged from current behaviour. The prohibition on the underlying model reference in FR-003 applies to the storage location only, not to the conversation document's contents.
- **FR-011**: A failure to create the conversation MUST NOT fail or alter the outcome of the schedule run that produced it; the failure MUST be recorded for diagnosis.
- **FR-012**: Conversations created before this change MUST remain accessible at their existing locations; the system MUST NOT relocate or rename them.

### Key Entities *(include if feature involves data)*

- **Schedule**: A user-configured task definition carrying a display name and a target deployment. Its name is used verbatim (URL-encoded) in the conversation location of every conversation created from its executions.
- **Deployment**: The execution target of a schedule — either a model or an application. Identified by the deployment id configured on the schedule. An application additionally has an underlying model reference, which is an internal implementation detail and must not appear in conversation locations.
- **Schedule Run**: A single execution of a schedule. Each successful run that produces a chat completion results in one conversation.
- **Conversation**: The chat record created from a schedule run, stored at a location composed of the owner's storage area, a scheduler folder, the schedule identifier, and a final segment identifying the deployment, the schedule name, and the run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of conversations created from application-backed schedule runs are stored at a location identifying the scheduled application, with zero occurrences of the application's underlying model reference in the location.
- **SC-002**: 100% of conversations created from schedule runs — model-backed and application-backed alike — have a deployment portion that decodes to the exact deployment id configured on the schedule.
- **SC-003**: 100% of conversations created from schedule runs have a name portion that decodes to the exact schedule name, including original case, spacing, punctuation, and non-Latin characters.
- **SC-004**: Schedules whose names differ only in case, spacing, or punctuation produce distinct, non-colliding conversation locations in 100% of tested cases.
- **SC-005**: Schedule runs whose deployment id or schedule name contains reserved or non-ASCII characters produce a successfully stored, well-formed conversation in 100% of tested cases — no run fails and no conversation lands at an unintended location.
- **SC-006**: For any conversation produced by a schedule run, the application or model and the schedule that produced it can be determined from the conversation's own display name and location alone, without consulting any other system.
- **SC-007**: Reopening a conversation produced by an application-backed schedule run continues to work exactly as it does today — no regression in how existing conversations open.

## Assumptions

- The conversation location already contains a run identifier and the schedule identifier, so this change does not need to guarantee uniqueness on its own — it only needs to guarantee accurate, lossless representation of the deployment id and schedule name.
- "Deployment id" means the identifier the schedule was configured with — the value the user selected — for both models and applications. "Reference id" means the underlying model reference that an application resolves to, and which must no longer appear in conversation locations.
- Standard percent-encoding (RFC 3986) is a sufficient and acceptable encoding scheme for both the deployment-id and schedule-name portions.
- The change applies to conversations created from this point forward; no backfill, migration, or relocation of existing conversations is in scope.
- Every schedule has a non-empty display name in normal operation; empty or whitespace-only names are treated as a defensive edge case rather than an expected input.
- The underlying conversation storage accepts percent-encoded segments and preserves them byte-for-byte, so a location written with encoded segments can be read back and decoded without loss.
- Truncation of over-long segments, if the storage system requires it, is an implementation concern and is not expected to affect the typical case.
- Decision (confirmed): the conversation's display name stays identical to the final location segment, so users will see percent-escapes in the chat list for names and application ids containing special characters. This was chosen deliberately over a separately-humanised display name, to keep name and location in lockstep.
- Decision (confirmed): resolving an application to its underlying model reference remains necessary, because the conversation document still records that reference as the deployment it reopens against. Only the storage location stops using it.

# Feature Specification: Run Conversation Naming Convention

**Feature Branch**: `031-run-conversation-naming`

**Created**: 2026-07-24

**Status**: Draft

**Input**: User description: "For created conversations use next naming convention `chathub-claude4__hello__964ff385-72e7-46f8-8cdc-fa3358b825ec` where — first part is slugified deployment id, second part slugified schedule display name, third part run id"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recognize a run's conversation by its name (Priority: P1)

A user who has enabled conversation creation for a schedule browses their DIAL Chat conversations and wants each auto-created conversation to carry a human-recognizable name that tells them, at a glance, which deployment it ran against, which schedule produced it, and which specific run it corresponds to — instead of an opaque identifier.

**Why this priority**: The recognizable name is the entire value of this feature. Without it, conversations are indistinguishable in the DIAL Chat list. It is a complete, demonstrable slice on its own.

**Independent Test**: Enable conversation creation on a chat completion schedule with a known deployment and display name, let a run complete, and confirm the created conversation's name is composed of the slugified deployment id, the slugified schedule display name, and the run id, joined in that order by the defined separator.

**Acceptance Scenarios**:

1. **Given** a schedule with deployment id "chathub-claude4" and display name "hello", **When** a run with id "964ff385-72e7-46f8-8cdc-fa3358b825ec" completes and its conversation is created, **Then** the conversation name is `chathub-claude4__hello__964ff385-72e7-46f8-8cdc-fa3358b825ec`.
2. **Given** a schedule whose display name contains spaces, uppercase letters, or punctuation, **When** the conversation is created, **Then** the display-name segment is slugified (lowercased, non-alphanumeric runs collapsed to a single hyphen, leading/trailing hyphens trimmed).
3. **Given** a schedule whose deployment id contains uppercase letters or separators, **When** the conversation is created, **Then** the deployment-id segment is slugified using the same rules.
4. **Given** any created conversation, **When** its name is produced, **Then** the run id segment is the run id preserved verbatim (not slugified), so the conversation remains traceable to its run.

---

### User Story 2 - Stable, unambiguous naming across edge inputs (Priority: P2)

A user with schedules whose display names are empty-after-slugification, non-Latin, or unusually long still expects every created conversation to receive a valid, non-colliding, readable name, so the naming scheme never fails a run's conversation creation.

**Why this priority**: Robustness against odd inputs prevents conversation creation from failing or producing confusing names, but it builds on the core naming slice in Story 1.

**Independent Test**: Create conversations for schedules with an empty-after-slugification display name, a very long display name, and a display name with only non-alphanumeric characters, and confirm each produces a valid, readable name whose run id segment still uniquely identifies the run.

**Acceptance Scenarios**:

1. **Given** a display name that slugifies to an empty string (e.g., only punctuation), **When** the conversation is created, **Then** a defined placeholder is used for that segment and the name is still valid and readable.
2. **Given** a very long display name, **When** the conversation is created, **Then** the display-name segment is truncated to the defined maximum length while remaining a valid slug.
3. **Given** two runs of the same schedule, **When** both conversations are created, **Then** their names differ because the run id segment differs.

---

### Edge Cases

- **Empty/whitespace deployment id**: the deployment part is treated the same as an empty-after-slugification display name (defined placeholder used). Deployment id is expected to always be present, so this is a defensive fallback only.
- **Display name that slugifies to empty**: a defined placeholder segment is used instead of an empty segment (avoids `deployment____runid`).
- **Separator characters appearing inside a segment**: because segments are slugified (hyphenated), a literal double-underscore cannot appear inside a slugified segment, so the three-part split by the separator remains unambiguous. The run id segment is not slugified but does not contain the double-underscore separator.
- **Very long segments**: deployment-id and display-name segments are truncated to a defined maximum length so the overall name stays within a reasonable bound; the run id is never truncated.
- **Existing behavior**: this changes only the conversation *name*; the conversation's storage id/path (used to address the document) is out of scope and unchanged.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST name each auto-created run conversation using three segments in fixed order: slugified deployment id, slugified schedule display name, run id.
- **FR-002**: The system MUST join the three segments with a double-underscore (`__`) separator, producing `{deployment-slug}__{display-name-slug}__{run-id}`.
- **FR-003**: The system MUST slugify the deployment-id and display-name segments by lowercasing, replacing each run of non-alphanumeric characters with a single hyphen, and trimming leading/trailing hyphens.
- **FR-004**: The system MUST include the run id as the final segment verbatim (without slugification) so the conversation remains traceable to its originating run.
- **FR-005**: When a segment (deployment id or display name) slugifies to an empty string, the system MUST substitute a defined non-empty placeholder for that segment.
- **FR-006**: The system MUST truncate the deployment-id and display-name segments to a defined maximum length (per segment) while keeping each a valid slug; the run id segment MUST NOT be truncated.
- **FR-007**: The system MUST apply this naming convention only when a conversation is created for a completed run; it MUST NOT change the conversation's storage id/path, its messages, or any other conversation attribute.
- **FR-008**: The naming logic MUST be deterministic — the same deployment id, display name, and run id always yield the same name.

### Key Entities *(include if feature involves data)*

- **Run Conversation Name**: The display name assigned to an auto-created DIAL conversation. Composed of a deployment-id slug, a display-name slug, and a verbatim run id, joined by a double-underscore separator.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of newly created run conversations have names matching the pattern `{deployment-slug}__{display-name-slug}__{run-id}`.
- **SC-002**: Given a conversation name, a user can identify the source deployment and schedule without opening the conversation in 100% of cases where the display name and deployment id were non-empty after slugification.
- **SC-003**: No two conversations produced by different runs share the same name (uniqueness guaranteed by the run id segment).
- **SC-004**: Conversation creation succeeds for 100% of runs regardless of display-name content (empty-after-slugification, non-Latin, or overly long inputs never cause a failure).

## Assumptions

- The deployment id used for the first segment is the schedule's target deployment (the `model` value from the schedule payload), as opposed to the resolved underlying model reference for application deployments.
- The schedule display name is always present (it is a required, non-empty field on a schedule); the empty-after-slugification handling is a defensive fallback for names consisting only of non-alphanumeric characters.
- The run id is a stable, unique identifier already assigned before conversation creation and is safe to embed verbatim (contains no double-underscore).
- The maximum per-segment length for the slugified segments is a reasonable default (e.g., 64 characters) chosen to keep names readable; the exact value will be fixed during planning.
- The placeholder for an empty-after-slugification segment is a short, defined constant (e.g., `untitled`), chosen during planning.
- Only the conversation's display name changes; the conversation storage path/id defined by the existing conversation-creation feature (`027-create-run-conversation`) is unchanged.

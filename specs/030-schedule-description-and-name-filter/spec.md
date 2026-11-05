# Feature Specification: Schedule Description & Name Filter

**Feature Branch**: `030-schedule-description-and-name-filter`

**Created**: 2026-07-24

**Status**: Draft

**Input**: User description: "1) Add description field for schedule. Set reasonable restriction for its length. Return description on schedules list and details. 2) Add filter by schedule name on schedules list. 3) Also add trigger to schedules list"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Describe a schedule (Priority: P1)

A user who manages many schedules wants to attach a free-text description to a schedule, so they and their teammates can record what the schedule is for, who owns the intent behind it, or any operational notes, beyond the short name.

**Why this priority**: Storing the description is the foundation for every other capability in this feature (displaying it on list and details). Without it, nothing else can be built or verified.

**Independent Test**: Can be fully tested by creating a schedule with a description, then retrieving that schedule's details and confirming the stored description is returned unchanged.

**Acceptance Scenarios**:

1. **Given** a user creating a new schedule, **When** they supply a description, **Then** the schedule is created and the description is persisted with it.
2. **Given** a user updating an existing schedule, **When** they supply a new description, **Then** the stored description is replaced with the supplied value.
3. **Given** a user creating a schedule, **When** they supply no description, **Then** the schedule is created with an empty/absent description and no error occurs.
4. **Given** a user creating or updating a schedule, **When** they supply a description longer than the allowed maximum length, **Then** the request is rejected with a validation error and the schedule is not created or modified.

---

### User Story 2 - See the description on details and list (Priority: P1)

A user retrieving a schedule's details, or browsing the paginated list of schedules, wants to see each schedule's description, so they can understand the purpose of a schedule without opening it individually.

**Why this priority**: Displaying the description is the user-visible value of the feature. It is required on both the details view and the list view per the request.

**Independent Test**: Can be fully tested by creating schedules with and without descriptions, then requesting both the details of one and the list of all, and confirming the description appears (or is empty) in both responses exactly as stored.

**Acceptance Scenarios**:

1. **Given** a schedule with a description, **When** a user requests that schedule's details, **Then** the response includes the description exactly as stored.
2. **Given** a schedule with a description, **When** a user requests the schedules list, **Then** each returned list row includes that schedule's description.
3. **Given** a schedule created without a description, **When** a user requests its details or sees it in the list, **Then** the description is returned as empty/absent without error.
4. **Given** schedules that existed before this feature, **When** a user requests their details or list, **Then** they are returned with an empty/absent description and no error.

---

### User Story 3 - Filter the schedules list by name (Priority: P2)

A user browsing the list of schedules wants to narrow the results to only those whose name matches a search term, so they can quickly find a specific schedule among many.

**Why this priority**: Filtering by name is a distinct convenience that delivers value on top of the existing list, but it is independent of the description work and can ship separately.

**Independent Test**: Can be fully tested by creating several schedules with differing names, then requesting the list with a name filter and confirming only the matching schedules are returned.

**Acceptance Scenarios**:

1. **Given** schedules named "Daily report" and "Hourly ping", **When** a user requests the list filtered by name "report", **Then** only "Daily report" is returned.
2. **Given** schedules with mixed-case names, **When** a user filters by name using different letter casing, **Then** matching is case-insensitive and the expected schedules are returned.
3. **Given** a user requesting the schedules list, **When** no name filter is supplied, **Then** the unfiltered (owner-scoped, paginated) list is returned as before.
4. **Given** a name filter that matches no schedule, **When** the list is requested, **Then** an empty result set is returned without error.
5. **Given** filtering is owner-scoped, **When** a user filters by name, **Then** only schedules owned by that user are eligible to match.

---

### User Story 4 - See the trigger on the list (Priority: P2)

A user browsing the schedules list wants to see each schedule's full trigger definition (not just whether it is cron- or date-based), so they can tell when a schedule fires without opening each schedule's details.

**Why this priority**: It improves the list's usefulness and is independent of the description and name-filter work, so it can ship separately.

**Independent Test**: Can be fully tested by creating schedules with cron and date triggers, then requesting the list and confirming each row includes the same full trigger definition returned by that schedule's details.

**Acceptance Scenarios**:

1. **Given** a schedule with a cron trigger, **When** a user requests the schedules list, **Then** the row includes the full cron trigger definition.
2. **Given** a schedule with a date trigger, **When** a user requests the schedules list, **Then** the row includes the full date trigger definition.
3. **Given** a schedule in the list, **When** a user compares the trigger shown on the list row to the trigger shown on that schedule's details, **Then** they are identical.

---

### Edge Cases

- **Empty name filter value**: A name filter supplied with an empty value is treated as no filter (returns the full owner-scoped list).
- **Whitespace-only description**: A description consisting only of whitespace is stored as supplied; it is not treated as invalid.
- **Description at exactly the maximum length**: A description exactly at the maximum allowed length is accepted; one character over is rejected.
- **Name filter combined with existing metadata filters**: A name filter and metadata filters supplied together compose with logical AND (a schedule must match all supplied filters).
- **Name filter combined with pagination**: The name filter narrows the result set before pagination is applied, so page counts reflect only matching schedules.
- **Unicode / multibyte descriptions**: The length restriction is measured in characters, so multibyte characters each count as one character.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow an optional free-text **description** to be associated with each schedule.
- **FR-002**: The system MUST persist a schedule's description so it survives across requests and restarts.
- **FR-003**: Users MUST be able to supply a description when creating a schedule, and the supplied description MUST be stored with the schedule.
- **FR-004**: Users MUST be able to supply a description when updating a schedule, and the supplied description MUST replace the previously stored description for that schedule.
- **FR-005**: When a description is not supplied on create, the system MUST treat the schedule's description as empty/absent rather than rejecting the request.
- **FR-006**: The system MUST enforce a maximum description length and MUST reject any create or update request whose description exceeds that maximum with a validation error, leaving the schedule unchanged.
- **FR-007**: The schedule details response MUST include the schedule's stored description.
- **FR-008**: Each row of the schedules list response MUST include the schedule's stored description.
- **FR-009**: The system MUST allow the schedules list to be filtered by schedule name via a query parameter.
- **FR-010**: Name filtering MUST match schedules whose name contains the supplied term as a substring, case-insensitively.
- **FR-011**: When no name filter is supplied (absent or empty value), the list endpoint MUST behave exactly as it does today (owner-scoped, paginated, and honoring any existing metadata filters).
- **FR-012**: Name filtering MUST remain owner-scoped: a user MUST only be able to match schedules they own.
- **FR-013**: Name filtering MUST compose with existing pagination and existing metadata filters so results are filtered consistently (logical AND across all supplied filters) and paginated correctly.
- **FR-014**: A name filter matching no schedule MUST return an empty result set without error.
- **FR-015**: Existing schedules created before this feature MUST be treated as having an empty/absent description and MUST continue to function unchanged.
- **FR-016**: Each row of the schedules list response MUST include the schedule's full trigger definition, identical to the trigger returned on that schedule's details response.
- **FR-017**: The trigger definition on the list MUST be provided in addition to the existing trigger-type indicator; existing list fields MUST NOT be removed.

### Key Entities *(include if feature involves data)*

- **Schedule**: An existing entity representing a scheduled job. This feature adds an optional **description** attribute to it. The schedule's existing **name** attribute becomes filterable on the list, and the schedule's existing **trigger** definition becomes visible on the list.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can create a schedule with a description and retrieve that exact description from both the schedule details and the schedules list, with 100% fidelity.
- **SC-002**: A create or update request with a description over the maximum length is rejected 100% of the time, with a clear validation error and no change to stored data.
- **SC-003**: A user filtering the schedules list by name receives only schedules whose name matches the term (case-insensitive substring), with zero non-matching schedules in the result.
- **SC-004**: Requests to the schedules list with no name filter return results identical to the pre-feature behavior (aside from the newly added description field on each row), confirming zero regression for existing consumers.
- **SC-005**: Schedules that existed before the feature remain retrievable, listable, and filterable with no errors, returning an empty/absent description.
- **SC-006**: For every schedule in the list, the trigger definition shown on its list row is identical to the trigger returned on its details response, in 100% of cases.

## Assumptions

- The schedule's existing display name is the "name" referred to for filtering; no separate name field is introduced.
- The description is **user-supplied** through the existing create and update schedule requests; the system does not derive or auto-populate it.
- Update **replaces** the description (consistent with the existing full-replacement update semantics for schedule fields) rather than merging.
- The maximum description length is **1000 characters**, chosen as a reasonable limit for free-text operational notes while preventing abuse; the length is measured in characters.
- Name filtering performs **case-insensitive substring** matching, which is the conventional behavior for a name search filter; exact-match, prefix-only, and fuzzy matching are out of scope.
- The name filter is exposed as a single query parameter on the existing list endpoint and composes with the already-existing metadata filters using AND semantics.
- An empty description is represented consistently (empty or absent) across create defaults, pre-existing schedules, and responses.
- No changes are made to how schedules are triggered, executed, paused, or resumed; this feature only affects the schedule's stored description and the list query surface.

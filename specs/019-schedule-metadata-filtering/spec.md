# Feature Specification: Schedule Metadata & Filtering

**Feature Branch**: `019-schedule-metadata-filtering`

**Created**: 2026-06-17

**Status**: Draft

**Input**: User description: "1) For schedules table add json column `metadata`. 2) Metadata should be displayed on schedule details endpoint. 3) Each key with string value from metadata can be used as filter for schedules list. Example: a schedule with `metadata: {\"tenant\": \"hcscd\"}` should be filterable by `/schedules/?metadata__tenant=hcscd`."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Attach business metadata to a schedule (Priority: P1)

A user who manages schedules wants to attach arbitrary business context (such as tenant, environment, owning team, or cost center) to a schedule as a set of key-value pairs, so the schedule can later be organized, retrieved, and filtered according to how their organization groups work.

**Why this priority**: Without the ability to store metadata, none of the other capabilities (display, filtering) are possible. This is the foundational slice.

**Independent Test**: Can be fully tested by creating a schedule with a metadata object, then retrieving it and confirming the stored metadata is returned unchanged.

**Acceptance Scenarios**:

1. **Given** a user creating a new schedule, **When** they supply a metadata object such as `{"tenant": "hcscd"}`, **Then** the schedule is created and the metadata is persisted with it.
2. **Given** a user updating an existing schedule, **When** they supply a new metadata object, **Then** the stored metadata is replaced with the supplied value.
3. **Given** a user creating a schedule, **When** they supply no metadata, **Then** the schedule is created with empty metadata and no error occurs.

---

### User Story 2 - View a schedule's metadata in its details (Priority: P1)

A user retrieving the details of a specific schedule wants to see the metadata associated with it, so they can confirm the business context attached to that schedule.

**Why this priority**: Viewing stored metadata is the minimum way to confirm that data was saved correctly and is essential for users to trust and use the feature.

**Independent Test**: Can be fully tested by requesting the details of a schedule that has metadata and verifying the metadata is present in the response exactly as stored.

**Acceptance Scenarios**:

1. **Given** a schedule with metadata `{"tenant": "hcscd", "env": "prod"}`, **When** a user requests that schedule's details, **Then** the response includes the full metadata object with both keys and values.
2. **Given** a schedule created without metadata, **When** a user requests its details, **Then** the response includes an empty metadata object.

---

### User Story 3 - Filter the schedules list by metadata (Priority: P2)

A user browsing the list of schedules wants to narrow the results to only those schedules whose metadata contains specific string values, so they can quickly find all schedules belonging to a tenant, environment, or other grouping.

**Why this priority**: Filtering delivers the primary organizational value of metadata, but it depends on Stories 1 and 2 being in place first.

**Independent Test**: Can be fully tested by creating several schedules with differing metadata, then requesting the list with a metadata filter and confirming only matching schedules are returned.

**Acceptance Scenarios**:

1. **Given** schedules with metadata `{"tenant": "hcscd"}` and `{"tenant": "other"}`, **When** a user requests the list with filter `metadata__tenant=hcscd`, **Then** only the schedule whose `tenant` value equals `hcscd` is returned.
2. **Given** schedules with various metadata, **When** a user supplies two metadata filters such as `metadata__tenant=hcscd&metadata__env=prod`, **Then** only schedules whose metadata matches all supplied key-value pairs are returned.
3. **Given** a user requesting the schedules list, **When** no metadata filter is supplied, **Then** the unfiltered (owner-scoped, paginated) list is returned as before.
4. **Given** a filter referencing a metadata key that no schedule has, **When** the list is requested, **Then** an empty result set is returned (no error).
5. **Given** filtering is owner-scoped, **When** a user filters by metadata, **Then** only schedules owned by that user are eligible to match.

---

### Edge Cases

- **Non-string metadata values**: A schedule may store metadata whose values are not strings (numbers, booleans, nested objects, arrays). Only keys whose value is a string are usable as filters. A filter against a non-string-valued key matches nothing.
- **Reserved / unknown query parameters**: Query parameters that begin with the `metadata__` prefix are interpreted as metadata filters; parameters without that prefix are not treated as metadata filters.
- **Filter key with no value**: A metadata filter supplied with an empty value matches schedules whose corresponding metadata value is an empty string.
- **Duplicate filter keys**: If the same `metadata__<key>` filter is supplied more than once, the behavior is defined by the resolved query-parameter handling (assumption documented below).
- **Metadata key containing the delimiter**: A metadata key that itself contains `__` is addressed by the parsing rule (everything after the first `metadata__` prefix is treated as the key).
- **Large metadata object**: Metadata is intended for lightweight business tags, not bulk payload storage (see Assumptions for size guidance).
- **Filtering matches exact value**: Metadata filtering matches the full string value exactly; partial/substring matches are out of scope.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow a metadata value — a map of string keys to arbitrary JSON values — to be associated with each schedule.
- **FR-002**: The system MUST persist a schedule's metadata so it survives across requests and restarts.
- **FR-003**: Users MUST be able to supply metadata when creating a schedule, and the supplied metadata MUST be stored with the schedule.
- **FR-004**: Users MUST be able to supply metadata when updating a schedule, and the supplied metadata MUST replace the previously stored metadata for that schedule.
- **FR-005**: When metadata is not supplied on create, the system MUST treat the schedule's metadata as an empty object rather than rejecting the request.
- **FR-006**: The schedule details response MUST include the schedule's full stored metadata object.
- **FR-007**: The system MUST allow the schedules list to be filtered by metadata using query parameters of the form `metadata__<key>=<value>`, where `<key>` is a metadata key and `<value>` is the expected string value.
- **FR-008**: When one or more metadata filters are supplied, the system MUST return only schedules whose metadata contains every supplied key with a string value exactly equal to the supplied value (logical AND across multiple filters).
- **FR-009**: Metadata filtering MUST only match keys whose stored value is a string; keys whose value is a non-string type MUST NOT match any metadata filter value.
- **FR-010**: When no metadata filter is supplied, the list endpoint MUST behave exactly as it does today (owner-scoped, paginated, unfiltered).
- **FR-011**: Metadata filtering MUST remain owner-scoped: a user MUST only be able to match schedules they own.
- **FR-012**: Metadata filtering MUST compose with the existing pagination so filtered results are paginated consistently.
- **FR-013**: A metadata filter referencing a key absent from all of the user's schedules MUST return an empty result set without error.
- **FR-014**: Existing schedules created before this feature MUST be treated as having empty metadata and MUST continue to function unchanged.

### Key Entities *(include if feature involves data)*

- **Schedule**: An existing entity representing a scheduled job. This feature adds a **metadata** attribute to it.
- **Schedule Metadata**: A map of string keys to JSON values attached to a schedule, holding lightweight business context (e.g., tenant, environment). Only string-valued entries participate in filtering.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can create a schedule with metadata and retrieve that exact metadata from the schedule details in a single round trip, with 100% fidelity of keys and values.
- **SC-002**: A user filtering the schedules list by a single metadata key-value pair receives only the schedules matching that pair, with zero non-matching schedules in the result.
- **SC-003**: A user supplying multiple metadata filters receives only schedules matching all supplied pairs (AND semantics) in 100% of cases.
- **SC-004**: Requests to the schedules list with no metadata filter return identical results to the pre-feature behavior, confirming zero regression for existing consumers.
- **SC-005**: Schedules that existed before the feature was introduced remain retrievable and filterable (matching only empty-metadata-compatible filters) with no errors.

## Assumptions

- Metadata is **user-supplied** through the existing create and update schedule requests; the system does not derive or auto-populate metadata.
- Update **replaces** the entire metadata object rather than merging individual keys, consistent with the existing update semantics (full replacement of schedule fields).
- Multiple metadata filters are combined with **AND** semantics; there is no OR or negation filtering in this scope.
- Metadata filtering performs **exact, case-sensitive, full-value string matching**; partial, prefix, or case-insensitive matching is out of scope.
- The `metadata__` prefix is the agreed delimiter convention for distinguishing metadata filters from other query parameters; the metadata key is everything following the first `metadata__` prefix.
- If the same `metadata__<key>` parameter is supplied more than once, the last supplied value is used (standard single-value query-parameter handling).
- Metadata is intended for lightweight tagging (small numbers of short keys/values), not for storing large documents; no explicit size limit is introduced in this scope beyond reasonable request-body limits already in place.
- The list response item shape is unchanged by this feature; metadata is exposed on the **details** endpoint only (not added to list rows) unless future scope requires it.

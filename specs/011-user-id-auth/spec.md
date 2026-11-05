# Feature Specification: User Identity Authentication for Schedule Endpoints

**Feature Branch**: `011-user-id-auth`

**Created**: 2026-06-11

**Status**: Draft

**Input**: User description: "Add authentication dependency and dynamic user_id extraction to all schedule and schedule run endpoints."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operate on my own schedules with my identity (Priority: P1)

An authenticated caller presents their access credential when calling any schedule or
schedule-run endpoint. The system determines who the caller is from that credential and
scopes every read and write to that caller's identity, so callers only ever create, view,
update, or list the schedules and runs that belong to them.

**Why this priority**: This is the core of the feature. Without resolving the caller's
identity from their credential, every endpoint operates on a hard-coded placeholder
identity, which means data is not isolated per user. This is the foundational capability
that makes the endpoints safe to expose.

**Independent Test**: Call a schedule endpoint with a valid credential, confirm the created
or returned data is associated with the identity resolved from that credential (and not the
former placeholder identity). Repeat with a different credential and confirm the two callers
see disjoint data.

**Acceptance Scenarios**:

1. **Given** a caller with a valid credential, **When** they create a schedule, **Then** the
   schedule is owned by the identity resolved from that credential.
2. **Given** two callers with different valid credentials, **When** each lists their schedules,
   **Then** each caller sees only the schedules associated with their own resolved identity.
3. **Given** a caller with a valid credential, **When** they request a run under a schedule they
   own, **Then** the run details are returned; **When** they request a schedule or run owned by
   a different identity, **Then** it is reported as not found.

---

### User Story 2 - Reject callers whose identity cannot be established (Priority: P1)

When a caller's credential is missing, invalid, or does not yield a usable identity, the
request is rejected rather than silently proceeding under a default identity.

**Why this priority**: Equally foundational — allowing unidentified callers through would
defeat the purpose of per-user scoping and expose other users' data.

**Independent Test**: Call any schedule or run endpoint without a credential, with an invalid
credential, and with a credential that resolves to an empty identity; confirm each is rejected
with an appropriate authorization error and no schedule data is read or written.

**Acceptance Scenarios**:

1. **Given** a request with no credential, **When** it reaches any schedule or run endpoint,
   **Then** the request is rejected as unauthorized and no data operation occurs.
2. **Given** a request whose credential is rejected by the DIAL service, **When** it
   reaches any endpoint, **Then** the request is rejected as unauthorized.
3. **Given** a request whose credential is accepted but yields no usable (non-empty) identity
   value, **When** it reaches any endpoint, **Then** the request is rejected as unauthorized.

---

### User Story 3 - Resolve identity from a configurable set of attributes (Priority: P2)

The identity value is read from the DIAL service's response using a configurable,
ordered list of candidate locations. The first candidate that yields a usable value wins; if
it is absent, the next candidate is tried, and so on.

**Why this priority**: Improves resilience and configurability. Different deployments expose
the caller's identity under different attributes; an ordered fallback avoids hard-coding a
single attribute. It builds on P1 but the system is still viable with a single default
location, so it is P2.

**Independent Test**: Configure the ordered candidate list, return a provider response where
the first candidate is absent but a later one is present, and confirm the later candidate's
value is used as the identity.

**Acceptance Scenarios**:

1. **Given** the first candidate location is present and non-empty in the provider response,
   **When** identity is resolved, **Then** that value is used.
2. **Given** the first candidate location is absent or empty but a later candidate is present
   and non-empty, **When** identity is resolved, **Then** the later candidate's value is used.
3. **Given** no candidate location yields a non-empty value, **When** identity is resolved,
   **Then** the request is rejected as unauthorized.

---

### Edge Cases

- A candidate location traverses into an attribute or array index that does not exist in the
  response → that candidate is skipped and the next is tried.
- A candidate location resolves to a value that is present but empty (e.g. empty string) →
  treated as not found and the next candidate is tried.
- A candidate path expects an array index but encounters a non-array value (or vice versa) →
  that candidate is skipped and the next is tried.
- The DIAL service is unreachable or returns an error/non-success response → the request
  is rejected; the caller is not silently admitted.
- The DIAL service responds successfully but with a malformed or unexpected body → no
  candidate yields a value, so the request is rejected.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST require every schedule and schedule-run endpoint (create, update,
  get, list schedules; list runs, get run) to operate under a caller identity resolved at
  request time, replacing the previously hard-coded placeholder identity.
- **FR-002**: The system MUST establish the caller's identity by querying the DIAL service's
  caller information endpoint using the credential supplied with the incoming request.
- **FR-003**: The system MUST extract the identity value from the DIAL service's response
  using an ordered, configurable list of candidate locations.
- **FR-004**: Each candidate location MUST support navigating nested attributes and array
  positions within the response to reach the identity value.
- **FR-005**: The system MUST try candidate locations in order and use the first one that yields
  a usable (present and non-empty) value as the caller identity.
- **FR-006**: The system MUST reject the request when no candidate location yields a usable
  identity value, or when the caller's credential is missing or rejected by the DIAL service,
  returning an authorization error and performing no schedule data operation.
- **FR-007**: The system MUST scope all schedule and schedule-run reads and writes to the
  resolved caller identity, so callers only access data associated with their own identity.
- **FR-008**: The configurable list of candidate identity locations MUST have a sensible
  default so the system is usable without explicit configuration.

### Key Entities *(include if feature involves data)*

- **Caller Identity**: The non-empty value that uniquely identifies the requesting user and
  scopes all schedule and run operations. Derived per request from the supplied credential.
- **Credential**: The authentication token (API key) supplied with the incoming request, used
  to query the DIAL service.
- **Identity Candidate Location List**: An ordered, configurable set of locations describing
  where, within the DIAL service's response, the identity value may be found.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of schedule and schedule-run endpoint operations are performed under an
  identity resolved from the caller's credential, with zero operations using the former
  placeholder identity.
- **SC-002**: Requests with a missing, invalid, or unresolvable identity are rejected 100% of
  the time, with no schedule data read or written.
- **SC-003**: Two distinct callers operating concurrently never observe each other's schedules
  or runs (complete data isolation across all listed endpoints).
- **SC-004**: When the primary identity location is unavailable in the DIAL response, the
  system still resolves identity from a configured fallback location in 100% of cases where a
  fallback value is present.

## Assumptions

- The DIAL service (already integrated with the application) exposes a caller information
  endpoint that returns the data needed to derive identity, and is reachable from the service.
- The caller's credential is supplied via the same API key mechanism already used elsewhere in
  the application and is forwarded as-is to the DIAL service.
- The default ordered list of candidate identity locations is `["userClaims.mail.0", "project"]`,
  where dot-separated segments are attribute names and numeric segments are array indices.
- "Usable identity" means a non-empty string value; whitespace-only or empty values are treated
  as not found.
- Authorization failures are surfaced to the caller as a standard unauthorized/forbidden
  response without leaking internal details of the DIAL service.
- The set of endpoints in scope is exactly the existing schedule and schedule-run endpoints; no
  new endpoints are introduced by this feature.

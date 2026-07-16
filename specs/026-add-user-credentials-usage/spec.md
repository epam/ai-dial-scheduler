# Feature Specification: Execute Schedules with Per-User Credentials

**Feature Branch**: `026-add-user-credentials-usage`

**Created**: 2026-07-14

**Status**: Draft

**Input**: User description: "Each schedule should be executed using user credentials. DIAL now provides an external-services sign-in feature; the scheduler obtains per-user credentials via the external-service OBO (on-behalf-of) credentials API and uses them for schedule execution instead of a shared service key."

## Overview

Today every scheduled request is executed with a single shared service API key (`dial_api_key`). This means all schedules run with the same identity regardless of who created them, which prevents per-user access control and auditing on downstream DIAL services.

DIAL now exposes an **external services** feature that lets users sign in to external services, and lets the scheduler retrieve on-behalf-of (OBO) credentials for a given user and service. This feature makes each schedule execute with the credentials of the user who created it, obtained fresh for every run.

Each schedule gains a `service_id` that identifies which external service the run authenticates against. Before each execution the scheduler requests OBO credentials for the schedule's owner and service; on success the returned auth header replaces the shared key on the outgoing request; on failure the run is recorded as an error carrying the credentials-lookup response.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Schedule runs with the creator's credentials (Priority: P1)

A user who has signed in to an external service through DIAL creates a schedule. When the schedule fires, the scheduler fetches OBO credentials for that user and service and performs the target request authenticated as that user rather than as the shared scheduler service account.

**Why this priority**: This is the core value of the feature — without it schedules cannot act on behalf of individual users, which is the entire goal. It is the minimum viable slice: a single schedule executing with per-user credentials.

**Independent Test**: Create a schedule for a user who has valid OBO credentials for the configured service, let it execute, and verify the outgoing request carries the OBO-returned auth header (not the shared `Api-Key`) and the run is recorded as successful.

**Acceptance Scenarios**:

1. **Given** a schedule whose owner has valid OBO credentials for its `service_id`, **When** the schedule executes, **Then** the scheduler retrieves the OBO credentials, sends the target request using the returned auth header, and records the run as `success`.
2. **Given** a schedule execution, **When** the outgoing request is built, **Then** no shared `Api-Key` header is present on the request — it is replaced by the OBO-provided header.
3. **Given** multiple schedules owned by different users, **When** they execute, **Then** each request is authenticated with its own owner's credentials.

---

### User Story 2 - Missing or invalid credentials fail the run clearly (Priority: P1)

When the scheduler cannot obtain OBO credentials for a schedule's owner and service (the user has not signed in, the service is unknown, or DIAL rejects the request), the run must not silently fall back to the shared key. Instead the run is marked as an error whose result captures the credentials-lookup response so the owner can diagnose it.

**Why this priority**: Silent fallback to a shared identity would be a security regression and would hide the real problem. Failing loudly with a diagnosable result is essential and ships together with P1 execution.

**Independent Test**: Create a schedule for a user who has no OBO credentials for the service, let it execute, and verify the run is marked `error` with the credentials-lookup response stored in the run result and no target request sent.

**Acceptance Scenarios**:

1. **Given** a schedule whose owner has no valid OBO credentials for its `service_id`, **When** the schedule executes, **Then** the run is marked `error`, the run result contains the credentials-lookup response, and the target request is NOT sent.
2. **Given** a credentials lookup that fails, **When** the run is recorded, **Then** the stored result identifies that the failure occurred during credential retrieval (distinguishable from a target-request failure).
3. **Given** a schedule created by a user whose stored owner identifier cannot be resolved to valid OBO credentials (e.g. a legacy schedule), **When** it executes, **Then** it is treated as a credentials failure per the scenarios above (best-effort; no special legacy handling).

---

### User Story 3 - Owners specify which external service a schedule uses (Priority: P2)

When creating a schedule, the owner specifies the external service (`service_id`) whose credentials should be used at execution time. Existing schedules created before this feature continue to work by being associated with the default `dial-oauth` service.

**Why this priority**: Selecting the service is required for the OBO lookup, but the mechanics of storing/backfilling it can be validated independently of the execution path.

**Independent Test**: Create a schedule via the API supplying a `service_id`, and confirm it is persisted; confirm a schedule created before the feature is associated with `dial-oauth` after migration.

**Acceptance Scenarios**:

1. **Given** an API create request that includes a `service_id`, **When** the schedule is created, **Then** the `service_id` is persisted with the schedule.
2. **Given** an API create request that omits `service_id`, **When** the request is processed, **Then** it is rejected as invalid (`service_id` is required on the API).
3. **Given** schedules that existed before this feature, **When** the data migration runs, **Then** each is assigned `service_id = "dial-oauth"`.
4. **Given** a schedule created through MCP where no service is specified, **When** it is created, **Then** it uses `dial-oauth` as instructed by the tool guidance.

---

### Edge Cases

- **Owner identifier mismatch**: Schedules created before the owner-identifier resolution changed store an email-style owner rather than the subject (`sub`) the OBO lookup expects. Such schedules are handled best-effort — if credentials cannot be resolved the run errors (User Story 2, Scenario 3). No bulk normalization of owner identifiers is performed.
- **Credentials valid but target request fails**: If credentials are obtained successfully but the downstream target request fails, that is a normal target-request failure and follows the existing retry/timeout and error-recording behavior — it is not a credentials error.
- **Credentials expiry between fetch and use**: Because credentials are fetched fresh at the start of each run (no caching), the window for expiry within a single run is minimal; expiry is surfaced as a target-request failure if it occurs.
- **Non-`Api-Key` auth header returned**: The OBO response specifies both the header name and value to use; the scheduler applies exactly that header rather than assuming `Api-Key`.
- **Retry behavior on credentials failure**: A credentials-retrieval failure marks the run as error; it does not consume the schedule's target-request retry budget (retries apply to the target request, not to credential lookup).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each schedule MUST carry a `service_id` identifying the external service whose credentials are used when the schedule executes.
- **FR-002**: The schedule-create API MUST require `service_id`; requests omitting it MUST be rejected as invalid.
- **FR-003**: A data migration MUST assign `service_id = "dial-oauth"` to every schedule that existed before this feature. This value is applied only to pre-existing schedules (it is not a default for newly created schedules on the API).
- **FR-004**: Schedules created through MCP MUST default to `service_id = "dial-oauth"` when no other service is specified, and the MCP tool guidance MUST instruct clients to use `dial-oauth` unless another service is required.
- **FR-005**: Before executing a schedule, the system MUST request on-behalf-of credentials for the schedule's owner and the schedule's `service_id` from DIAL's external-service OBO credentials capability.
- **FR-006**: The credentials request MUST identify the target as the scheduler application and the requested service (application `scheduler_app_id`, external service `service_id`) and the credential owner (the schedule's owner identifier).
- **FR-007**: When credentials are successfully obtained, the system MUST apply the returned auth header (its name and value) to the outgoing target request.
- **FR-008**: When credentials are successfully obtained, the system MUST NOT include the shared scheduler service key (`Api-Key`) on any outgoing target request; the OBO-provided header replaces it across all request types (REST, SSE, chat completion).
- **FR-009**: When credentials cannot be obtained, the system MUST mark the run as `error`, MUST store the credentials-lookup response in the run result, and MUST NOT send the target request.
- **FR-010**: A credentials-retrieval error MUST be distinguishable in the stored run result from a target-request error.
- **FR-011**: Credentials MUST be fetched fresh for every schedule execution (no caching of credentials between runs).
- **FR-012**: The schedule owner identifier used for the credentials request MUST be resolved using the configured owner-identifier resolution order, updated to `["sub", "project"]`.
- **FR-013**: Configuration MUST provide a `scheduler_app_id` value (default `dial_scheduler`) used to construct the credentials request target.
- **FR-014**: Legacy schedules whose stored owner identifier cannot be resolved to valid credentials MUST be handled best-effort — they fail per FR-009 rather than through any special-case migration of the owner identifier.

### Key Entities *(include if data involved)*

- **Schedule**: The definition of a scheduled request. Gains a `service_id` attribute identifying the external service used for credential retrieval at execution time. Retains its existing owner identifier (who the schedule runs as) and target-request definition (URL, method, headers, body).
- **Schedule Run**: A record of one execution attempt. Its result now also captures credentials-retrieval failures (distinct from target-request failures) with the credentials-lookup response.
- **OBO Credentials**: The per-user, per-service credentials returned by DIAL, consisting of an auth header name and value to apply to the outgoing request. Not persisted between runs.
- **External Service**: A DIAL-registered service (identified by `service_id`, e.g. `dial-oauth`) that a user has signed in to and against which OBO credentials are issued.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of schedule executions authenticate using credentials obtained on behalf of the schedule's owner; no execution uses the shared service key on the outgoing target request.
- **SC-002**: When a schedule's owner lacks valid credentials for its service, 100% of such runs are recorded as errors that include the credentials-lookup response, and no target request is sent.
- **SC-003**: 100% of schedules that existed before the feature are associated with the `dial-oauth` service after migration, and continue to execute (succeeding or failing) without manual intervention.
- **SC-004**: Every new schedule created through the API is associated with an explicitly supplied service; create requests without a service are rejected 100% of the time.
- **SC-005**: An operator can distinguish a credentials-retrieval failure from a target-request failure for 100% of failed runs by inspecting the run result.

## Assumptions

- The DIAL external-service OBO credentials capability returns, on success, an auth header name and value to apply to outgoing requests, and returns a non-success response (surfaced to the caller) when the user has no valid credentials for the service.
- The schedule's existing owner identifier (`created_by`) is the correct subject to pass as the credential owner; going forward it is resolved via the updated `["sub", "project"]` order.
- Credentials are short-lived and cheap enough to fetch once per run; per-run retrieval is acceptable for expected execution volumes.
- The three existing execution paths (REST, SSE, chat completion) are all in scope for replacing the shared key with per-user credentials.
- A credentials-retrieval failure is terminal for that run and does not trigger the target-request retry policy.
- Existing legacy schedules whose owner identifier does not match a resolvable subject are acceptable to fail until recreated; no bulk backfill of owner identifiers is required.
- Requires access to the existing DIAL integration (base URL and shared service key) to authenticate the OBO credentials request itself.

# Feature Specification: MCP Endpoint Exposure

**Feature Branch**: `016-mcp-endpoint-exposure`

**Created**: 2026-06-16

**Status**: Draft

**Input**: User description: "Expose existing api endpoints via MCP using FastMCP.from_openapi. MCP calls will include API-KEY header, propagate this header to endpoints."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operate schedules from an MCP-capable assistant (Priority: P1)

A user working inside an MCP-capable AI assistant (for example, a DIAL conversation or any MCP client) wants to create, inspect, update, pause, resume, and list their scheduled tasks and review their run history without leaving the assistant. The assistant connects to the Scheduler's MCP surface, discovers the available operations as tools, and invokes them on the user's behalf. Each existing REST capability of the Scheduler is reachable as an MCP tool.

**Why this priority**: This is the core value of the feature — making the already-built scheduling capabilities usable from MCP clients. Without it there is no feature. It is independently demonstrable end-to-end the moment a single endpoint is reachable as an MCP tool.

**Independent Test**: Connect an MCP client to the Scheduler's MCP endpoint, list the exposed tools, and confirm that the full set of existing schedule and run operations appear as callable tools with names, descriptions, and input/output shapes that match the existing API contract. Invoke a read operation (e.g., list schedules) and confirm a correct result is returned.

**Acceptance Scenarios**:

1. **Given** the Scheduler MCP surface is running, **When** an MCP client requests the list of available tools, **Then** every existing API operation (create, update, get, list schedules; pause; resume; list/get runs) is presented as a distinct MCP tool with a name and description derived from the existing API contract.
2. **Given** a valid identity credential is supplied, **When** the client invokes the "create schedule" tool with valid input, **Then** a schedule is created and the same response the REST endpoint would return is delivered back through MCP.
3. **Given** a valid identity credential is supplied, **When** the client invokes the "list schedules" tool, **Then** only schedules owned by the identity behind the supplied credential are returned.

---

### User Story 2 - Caller identity is preserved through MCP (Priority: P1)

When an MCP client calls a tool, it supplies the same API key credential used for the REST API. That credential must be carried through to the underlying operation so the operation runs as the correct user, enforces ownership, and produces identity-scoped results — exactly as a direct REST call would.

**Why this priority**: Without credential propagation the exposed tools either fail authentication or, worse, operate without proper identity scoping. Correct, secure identity handling is mandatory for the feature to be usable at all.

**Independent Test**: Invoke an identity-scoped tool (e.g., list schedules) with a credential belonging to one user and confirm the results match that user's data only; repeat with a different user's credential and confirm results differ accordingly; invoke with a missing or invalid credential and confirm the call is rejected with an authentication error rather than executing.

**Acceptance Scenarios**:

1. **Given** an MCP tool call carries a valid API key, **When** the tool executes, **Then** the underlying operation receives that same credential and resolves the caller's identity identically to a direct REST call.
2. **Given** an MCP tool call carries no API key, **When** the tool is invoked, **Then** the call is rejected with an authentication error and no scheduling state is changed.
3. **Given** an MCP tool call carries an API key that cannot be resolved to an identity, **When** the tool is invoked, **Then** the call is rejected with an authentication error consistent with the REST behavior.
4. **Given** two different users each invoke the same tool with their own credentials, **When** the tool returns owner-scoped data, **Then** each user sees only their own data.

---

### User Story 3 - MCP surface stays in sync with the API contract (Priority: P2)

As the existing API evolves (endpoints added, parameters changed, schemas updated), the MCP tool surface reflects those changes without a separate, manually maintained definition. The MCP tools are derived from the existing API contract so the two cannot drift apart.

**Why this priority**: Reduces long-term maintenance cost and prevents the MCP surface from silently diverging from the REST API. It is valuable but not required for the initial working slice, hence P2.

**Independent Test**: Confirm that the set of exposed MCP tools is generated from the existing API contract definition rather than hand-authored, so that an endpoint present in the contract appears as a tool and an endpoint absent from it does not.

**Acceptance Scenarios**:

1. **Given** the API contract describes a set of operations, **When** the MCP surface is initialized, **Then** the exposed tools correspond one-to-one with the operations in that contract.
2. **Given** an operation exists in the API contract, **When** the tool list is inspected, **Then** that operation's parameters, request body, and response shape are represented faithfully in the corresponding tool.

---

### Edge Cases

- **Missing credential**: An MCP tool call without the API key header is rejected with an authentication error; no operation executes.
- **Invalid / unresolvable credential**: A call whose credential cannot be resolved to an identity is rejected consistently with the REST API's behavior (authentication failure rather than partial execution).
- **Identity provider unavailable**: When the upstream identity resolution service is unreachable, the tool call fails with an upstream/dependency error rather than executing unauthenticated.
- **Underlying operation error**: When an operation returns a domain error (e.g., schedule not found, invalid input, registration failure), the corresponding MCP tool surfaces a faithful, distinguishable error to the client rather than a generic failure.
- **Endpoints not intended for MCP**: Operational/utility endpoints (e.g., health checks, echo/demo endpoints) should not be presented as MCP tools.
- **Pagination**: List operations that page results expose paging controls and metadata through the tool in a way the client can use to retrieve subsequent pages.
- **Header naming**: The credential header carried by MCP calls uses the same header name the REST API already expects.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose the existing Scheduler API operations as MCP tools that an MCP client can discover and invoke.
- **FR-002**: The set of exposed MCP tools MUST be derived from the existing API contract definition so that the MCP surface stays consistent with the REST API without a separately maintained tool definition.
- **FR-003**: Each exposed tool MUST present a name, description, input parameters, and result shape faithful to the corresponding API operation's contract.
- **FR-004**: The system MUST accept an API key credential supplied with each MCP tool call using the same header name the REST API already requires.
- **FR-005**: The system MUST propagate the caller-supplied API key credential to the underlying operation so identity resolution and ownership scoping behave identically to a direct REST call.
- **FR-006**: The system MUST reject any tool call that lacks a credential, or whose credential cannot be resolved to an identity, with an authentication error, and MUST NOT change any scheduling state for such calls.
- **FR-007**: Owner-scoped operations invoked through MCP MUST return only data belonging to the identity resolved from the supplied credential.
- **FR-008**: The system MUST surface domain and validation errors from underlying operations to the MCP client in a way that distinguishes error conditions (e.g., not found, invalid input, upstream unavailable) rather than collapsing them into a single generic failure.
- **FR-009**: The system MUST exclude operational and demonstration endpoints (e.g., health check, echo/demo endpoints) from the exposed MCP tool set.
- **FR-010**: The MCP surface MUST cover the full set of user-facing schedule operations (create, update, get, list, pause, resume) and run-tracking operations (list/get runs) currently offered by the REST API.
- **FR-011**: The credential carried through MCP MUST NOT be logged, echoed back to the client, or otherwise exposed.
- **FR-012**: Exposing operations via MCP MUST NOT alter the behavior, contract, or availability of the existing REST endpoints.

### Key Entities *(include if feature involves data)*

- **MCP Tool**: A discoverable, invokable representation of a single existing API operation. Carries a name, description, input definition, and result definition derived from the API contract.
- **Caller Credential (API Key)**: The identity-bearing secret supplied with each tool call, used downstream to resolve the caller's identity and scope results. Must be propagated, never persisted or exposed.
- **Resolved Identity**: The user identity derived from the credential; governs ownership and the data a tool call may read or modify.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of the user-facing REST operations in scope (schedule create/update/get/list/pause/resume and run list/get) are invokable as MCP tools.
- **SC-002**: For every in-scope operation, a tool call with a valid credential produces a result equivalent to the result of the corresponding direct REST call for the same identity and input.
- **SC-003**: 100% of tool calls made without a valid, resolvable credential are rejected with an authentication error and result in zero changes to scheduling state.
- **SC-004**: Owner-scoping is exact: a tool call returns 0 records belonging to any identity other than the one resolved from the supplied credential.
- **SC-005**: Excluded endpoints (health check, echo/demo) appear as 0 tools in the MCP tool list.
- **SC-006**: The credential value appears in 0 log entries and 0 tool responses.
- **SC-007**: After a change to the API contract's operation set, the MCP tool list reflects that change with no manual edit to a separate tool definition.

## Assumptions

- The existing REST API already publishes a machine-readable contract (OpenAPI) that comprehensively and accurately describes the operations to be exposed; the MCP surface is generated from it (per the user input referencing `FastMCP.from_openapi`).
- The credential header name carried by MCP calls is the same one the REST API already uses (`Api-Key`).
- Identity resolution continues to be performed by the existing upstream DIAL identity mechanism; this feature reuses it rather than introducing a new authentication scheme.
- "Existing api endpoints" refers to the user-facing schedule and run operations; operational endpoints (health check) and demonstration endpoints (echo) are intentionally out of scope for exposure.
- The MCP surface is an additional access path layered over the existing operations; the REST API remains available and unchanged.
- MCP clients are responsible for supplying a valid credential on each call; the system does not store or manage credentials on the client's behalf.
- The transport/hosting choice for the MCP surface (e.g., mounted alongside the existing service) is an implementation decision to be settled during planning and does not change the requirements above.

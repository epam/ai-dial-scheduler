# Phase 1 Data Model: MCP Endpoint Exposure

This feature introduces **no new persistent data** and **no database migration**. It is a contract-derived access layer over existing endpoints. The "entities" below are runtime/conceptual constructs, documented for clarity.

## Persistence impact

- **New tables**: none
- **Modified tables**: none
- **Alembic migration**: not required
- **ORM model changes**: none

The existing `Schedule` and `ScheduleRun` models, repositories, and services are reused unchanged through the regular REST endpoints; MCP tool calls reach them via the in-process backing client.

## Conceptual / runtime entities

### MCP Tool (generated, not persisted)

A discoverable, invokable representation of one existing API operation, produced by `FastMCP.from_openapi`.

| Attribute | Source | Notes |
|-----------|--------|-------|
| name | OpenAPI `operationId` (optionally remapped via `mcp_names`) | One per exposed operation |
| description | OpenAPI summary/description | Derived from endpoint docstrings |
| input schema | OpenAPI path/query params + request body schema | Faithful to REST contract (FR-003) |
| output schema | OpenAPI response schema | Faithful to REST contract (FR-003) |
| backing call | Method + path template | Executed via in-process ASGI httpx client |

**Lifecycle**: built once at app startup from `app.openapi()`; never stored. The set of tools is a pure function of the OpenAPI contract filtered by route maps (FR-002, SC-007).

### Exposed operation inventory (in scope)

| Operation | Method + Path | MCP type | Owner-scoped |
|-----------|---------------|----------|--------------|
| create_schedule | POST `/v1/schedules/` | Tool | yes |
| update_schedule | PUT `/v1/schedules/{schedule_id}` | Tool | yes |
| list_schedules | GET `/v1/schedules/` | Tool | yes (paginated) |
| get_schedule | GET `/v1/schedules/{schedule_id}` | Tool | yes |
| pause_schedule | POST `/v1/schedules/{schedule_id}/pause` | Tool | yes |
| resume_schedule | POST `/v1/schedules/{schedule_id}/resume` | Tool | yes |
| list_runs | GET `/v1/schedules/{schedule_id}/runs` | Tool | yes (paginated) |
| get_run | GET `/v1/schedules/{schedule_id}/runs/{run_id}` | Tool | yes |

**Excluded** (route-map EXCLUDE, FR-009): DIALApp health check; `echo` router (`/echo/rest`, `/echo/sse`) when `echo_endpoints` is enabled.

### Caller Credential (`Api-Key`) — transient

| Property | Value |
|----------|-------|
| Carried on | Each incoming MCP HTTP request header `Api-Key` |
| Read via | `get_http_headers()` during tool execution |
| Propagated by | httpx request event hook → injected as `Api-Key` on the outgoing backing request |
| Persistence | none — never stored |
| Logging | forbidden — value never logged or echoed (FR-011, SC-006) |
| Validation | none locally; validated downstream by the unchanged DIAL identity resolution |

### Resolved Identity — transient

Derived downstream by the existing `get_user_id` dependency (DIAL `/v1/user/info` → `user_id_path_list`). Governs ownership/scoping (FR-007, SC-004). Unchanged by this feature; it simply receives the forwarded `Api-Key`.

## State & flow (no state machine)

There are no new state transitions. The runtime flow per MCP tool call:

```
MCP client → (Api-Key header) → mounted MCP app
  → generated tool resolves → backing httpx client (ASGITransport)
    → request event hook reads get_http_headers(), injects Api-Key
      → re-enters DIALApp REST route → get_user_id (DIAL identity)
        → existing service/repository → response
  ← response mapped back to MCP tool result (success or faithful error)
```

## Error mapping (FR-008)

Underlying REST status codes are surfaced as distinguishable MCP tool errors rather than a single generic failure:

| Underlying condition | REST status | MCP tool result |
|----------------------|-------------|-----------------|
| Validation error | 422 | tool error: invalid input |
| Missing/unresolvable credential | 401 / missing header → 422 | tool error: authentication failure; no state change |
| DIAL unavailable | 502 | tool error: upstream/dependency unavailable |
| Schedule/run not found | 404 | tool error: not found |
| Job registration failure | 502 | tool error: upstream/dependency failure |
| Success | 2xx | tool result with response payload |

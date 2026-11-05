# Contract: MCP Tool Surface

The MCP surface is **generated** from the API service's OpenAPI document (`DIALApp.openapi()`) via `FastMCP.from_openapi`. This file is the human-readable contract for what that generation MUST produce and how authentication is carried. The authoritative, machine-readable schemas for each tool's input/output are the OpenAPI component schemas already published by the service — they are not duplicated here (FR-002).

## Transport & mount contract

- The MCP server is mounted on the existing `DIALApp` at `settings.mcp_path` (default `/mcp`).
- The mounted MCP app's lifespan is threaded into the parent app lifespan.
- The existing REST API at `/v1/...` is unchanged and remains available (FR-012).

## Authentication contract

- Every MCP tool call MUST carry the header `Api-Key: <credential>` (same name/semantics as the REST API).
- The credential is forwarded unchanged to the backing endpoint call and resolved by the existing DIAL identity mechanism.
- A call missing the header, or whose credential cannot be resolved to an identity, MUST be rejected (authentication failure) with **no** change to scheduling state (FR-006).
- The credential MUST NOT appear in logs or tool responses (FR-011).

## Tool inventory (MUST match exactly)

The generated tool set MUST be exactly the following 8 tools — no more (health/echo excluded), no fewer.

| Tool (operation) | Backing call | Input (from OpenAPI) | Result (from OpenAPI) |
|------------------|--------------|----------------------|-----------------------|
| create_schedule | POST `/v1/schedules/` | `ScheduleRequest` body | `ScheduleResponse` |
| update_schedule | PUT `/v1/schedules/{schedule_id}` | path `schedule_id`, `ScheduleRequest` body | `ScheduleResponse` |
| list_schedules | GET `/v1/schedules/` | pagination query params | `Page[ScheduleListItem]` |
| get_schedule | GET `/v1/schedules/{schedule_id}` | path `schedule_id` | `ScheduleResponse` |
| pause_schedule | POST `/v1/schedules/{schedule_id}/pause` | path `schedule_id` | `ScheduleResponse` |
| resume_schedule | POST `/v1/schedules/{schedule_id}/resume` | path `schedule_id` | `ScheduleResponse` |
| list_runs | GET `/v1/schedules/{schedule_id}/runs` | path `schedule_id`, pagination query params | `Page[RunListItem]` |
| get_run | GET `/v1/schedules/{schedule_id}/runs/{run_id}` | path `schedule_id`, `run_id` | `RunResponse` |

> Tool names follow the generated `operationId`; exact strings may be normalized by FastMCP. The contract requirement is one tool per row above, with input/output faithful to the referenced OpenAPI schemas.

## Exclusion contract (MUST NOT expose)

| Excluded | Reason |
|----------|--------|
| DIALApp health check | Operational endpoint (FR-009) |
| `POST /echo/rest`, `POST /echo/sse` | Demonstration endpoints (FR-009); only present when `echo_endpoints` is enabled, and excluded either way |

## Error contract (FR-008)

Tool errors MUST be distinguishable by underlying condition (validation / authentication / not-found / upstream-unavailable / success), mirroring the REST status codes. See `data-model.md` → "Error mapping".

## Verifiable acceptance (maps to Success Criteria)

- **SC-001 / SC-005**: tool-listing returns exactly the 8 tools above; 0 excluded endpoints present.
- **SC-002**: each tool call with a valid `Api-Key` returns a result equivalent to the corresponding direct REST call for the same identity + input.
- **SC-003**: calls without a valid/resolvable `Api-Key` are rejected; 0 scheduling-state changes.
- **SC-004**: owner-scoped tools return 0 records belonging to another identity.
- **SC-006**: `Api-Key` appears in 0 log entries and 0 tool responses.

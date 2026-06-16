# Phase 0 Research: MCP Endpoint Exposure

All Technical Context unknowns are resolved below. Each item is recorded as Decision / Rationale / Alternatives considered.

## R1. OpenAPI → MCP generation mechanism

**Decision**: Use `FastMCP.from_openapi(openapi_spec=<app openapi dict>, client=<backing httpx.AsyncClient>, name=..., route_maps=[...])` to generate the MCP server from the running service's own OpenAPI document (`DIALApp.openapi()`).

**Rationale**: `from_openapi` converts every OpenAPI operation into an MCP component; the default route mapping turns **all** operations into MCP **Tools** (`DEFAULT_ROUTE_MAPPINGS = [RouteMap(mcp_type=MCPType.TOOL)]`), which is the most broadly compatible representation for MCP clients. Generating from the live `app.openapi()` means the MCP surface is derived from the same contract FastAPI publishes — satisfying FR-002 (no separately maintained tool list) and SC-007 (surface tracks contract changes automatically).

**Alternatives considered**:
- *Hand-author one MCP tool per endpoint*: rejected — duplicates the contract, drifts over time, violates DRY and FR-002.
- *Generate from a committed static `openapi.json`*: rejected — risks divergence from the live app; the live `app.openapi()` is authoritative and already maintained.

## R2. Selecting which endpoints become tools (excluding health/echo)

**Decision**: Pass explicit `route_maps` to `from_openapi`, evaluated in order (first match wins):
1. `RouteMap(tags={"schedules", "runs"}, mcp_type=MCPType.TOOL)` — expose the two user-facing routers.
2. `RouteMap(mcp_type=MCPType.EXCLUDE)` — catch-all excluding everything else (DIALApp health check, `echo` router when enabled).

**Rationale**: The `schedules` and `runs` routers already carry distinct OpenAPI `tags`; matching on tags is stable against path changes. A trailing EXCLUDE map guarantees only the intended surface is exposed (FR-009, SC-005), regardless of whether `echo_endpoints` is on.

**Alternatives considered**:
- *Path-pattern excludes for `/health` and `/echo`*: rejected — more brittle than tag-based allow-list + catch-all exclude.
- *Build a filtered OpenAPI dict before passing in*: rejected — more code; route maps are the first-class FastMCP mechanism.

## R3. Per-request Api-Key propagation (the critical risk)

**Decision**: Forward the caller's `Api-Key` dynamically per tool call by combining:
- `get_http_headers()` from `fastmcp.server.dependencies` — returns the **incoming** MCP HTTP request headers during tool execution (returns `{}` when no HTTP request is active; never raises), and
- an `httpx` **request event hook** on the backing client that reads `get_http_headers()` and injects `Api-Key` onto the outgoing request to the underlying endpoint.

Sketch:
```python
from fastmcp.server.dependencies import get_http_headers
from src.lib.constants import API_KEY_HEADER  # "Api-Key"

async def forward_api_key(request: httpx.Request) -> None:
    incoming = get_http_headers()              # header names are lower-cased
    api_key = incoming.get(API_KEY_HEADER.lower())
    if api_key:
        request.headers[API_KEY_HEADER] = api_key

client = httpx.AsyncClient(..., event_hooks={"request": [forward_api_key]})
```

**Rationale**: `from_openapi`'s own `headers=` argument supports only **static** headers — unusable for multi-tenant per-call credentials. The documented dynamic pattern is to read incoming headers with `get_http_headers()` and inject them via an httpx event hook, which applies uniformly to *every* generated tool without per-tool code (satisfies FR-005 across all operations). `get_http_headers()` by default strips `authorization`/`content-length` but **not** `api-key`, so the credential survives; we still read it explicitly by name. This keeps the existing `get_user_id` DIAL dependency working unchanged downstream.

**Alternatives considered**:
- *Static `headers=` / a hard-coded token on the backing client*: rejected — not multi-tenant; would run all calls as one identity, violating FR-007/SC-004.
- *Custom `httpx.Auth` class*: viable, but an event hook is simpler and equally uniform (KISS).
- *Per-tool manual wrapper functions reading `ctx.get_http_request()`*: rejected — abandons generation, reintroduces hand-maintained tools (violates FR-002).

**Verification owed in implementation**: assert that `Api-Key` is not stripped by the installed FastMCP version's `get_http_headers()` defaults; if a future version strips it, switch to `get_http_headers(include={"api-key"})` or `include_all=True` (read explicitly, still never logged).

## R4. Backing transport to the underlying endpoints

**Decision**: Back the generated tools with an in-process `httpx.AsyncClient` using `httpx.ASGITransport(app=<the DIALApp>)` and a synthetic base URL (e.g. `http://scheduler.internal`). Tool calls re-enter the same ASGI app and hit the real `/v1/schedules…` routes in-process.

**Rationale**: No extra network hop, no second port to expose, and `app.state` (HTTP client, scheduler) is shared because it is the same app object. Paths in `app.openapi()` already include the `/v1` prefix, so generated requests target the correct routes. Reuses existing `ApiHttpSettings` timeouts/limits (Reliability I).

**Alternatives considered**:
- *Loopback HTTP to `http://localhost:<port>`*: rejected — adds a real network hop, couples to the bound port, and needs the server fully up before the client works.
- *Calling service-layer functions directly instead of HTTP*: rejected — bypasses the published contract and the `get_user_id` dependency chain, contradicting the "expose existing endpoints" intent and FR-005.

## R5. Mounting the MCP server into the existing DIALApp (lifespan gotcha)

**Decision**: Build the MCP ASGI app via `mcp.http_app(path="/")` and mount it on the `DIALApp` at a configurable path (default `/mcp`) using `app.mount(settings.mcp_path, mcp_app)`. The MCP app's lifespan **must** be threaded into the parent app's lifespan (combine the existing `lifespan` context manager with `mcp_app.lifespan`) so the MCP session manager starts/stops correctly.

**Rationale**: A mounted FastMCP HTTP app maintains its own lifespan (session manager/task group). FastAPI does not run a mounted sub-app's lifespan automatically; failing to thread it through causes runtime errors on first MCP request. `DIALApp` subclasses FastAPI, so `.mount()`, `.openapi()`, and a combined `lifespan` all work.

**Alternatives considered**:
- *Run MCP as a standalone process/service*: rejected — second deployable, second OpenAPI source, duplicated telemetry config; contradicts the single-service simplicity goal and complicates in-process backing transport.

## R6. Feature toggle & configuration

**Decision**: Add `mcp_enabled: bool = True` (or default off per ops preference) and `mcp_path: str = "/mcp"` to `Settings`. Build and mount the MCP sub-app only when enabled, mirroring the existing `echo_endpoints` toggle pattern.

**Rationale**: Consistent with the codebase's existing optional-feature pattern; lets ops disable the surface without code changes; keeps the change non-invasive to existing REST behavior (FR-012).

**Alternatives considered**: Always-on with no toggle — rejected; an explicit switch is cheap and aids rollout/rollback.

## R7. New dependency: `fastmcp` on Python 3.14

**Decision**: Add `fastmcp` to `[project.dependencies]` via `uv add fastmcp`; commit the updated lockfile.

**Rationale**: Required for `from_openapi` and `get_http_headers`. FastMCP targets Python ≥3.10.

**Verification owed in implementation**: confirm `uv add fastmcp` resolves cleanly on this project's Python 3.14 pin and that `mypy`/`ruff` gates pass with it installed. If a transitive dependency lacks 3.14 wheels, pin a compatible FastMCP version and record it. This is the single highest-uncertainty item and must be validated before tasks proceed.

## R8. Observability

**Decision**: Reuse `src.lib.utils.logging.logger` for MCP mount/startup and tool-invocation logs; rely on the parent `DIALApp` `TelemetryConfig` (OTLP traces/metrics) since the MCP app is mounted within it. Never log header values.

**Rationale**: Satisfies Principle II without new infrastructure; the credential-redaction requirement (FR-011/SC-006) is met by logging only metadata (tool name, decision), never `Api-Key`.

**Alternatives considered**: Separate telemetry pipeline for MCP — rejected (YAGNI; mounting inherits the app's telemetry).

## Sources

- [OpenAPI 🤝 FastMCP — integration guide](https://gofastmcp.com/integrations/openapi)
- [fastmcp.server.dependencies (get_http_headers) — API reference](https://gofastmcp.com/python-sdk/fastmcp-server-dependencies)
- [Discussion #722 — using get_http_headers with from_openapi](https://github.com/jlowin/fastmcp/discussions/722)
- [Discussion #344 / Issue #346 — per-request headers with from_openapi](https://github.com/jlowin/fastmcp/discussions/344)
- [Issue #590 — header not forwarded by OpenAPI server](https://github.com/jlowin/fastmcp/issues/590)

# Implementation Plan: MCP Endpoint Exposure

**Branch**: `016-mcp-endpoint-exposure` | **Date**: 2026-06-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/016-mcp-endpoint-exposure/spec.md`

## Summary

Expose the existing user-facing Scheduler REST operations (schedule create/update/get/list/pause/resume and run list/get) as MCP tools, generated from the service's own OpenAPI contract via `FastMCP.from_openapi`. The generated MCP server is mounted onto the existing `DIALApp` so it shares the same process, telemetry, and HTTP client. Each MCP tool call carries the same `Api-Key` header the REST API already requires; that header is forwarded per-request to the underlying endpoint call (via FastMCP's `get_http_headers()` read inside an `httpx` request event hook on the backing client) so identity resolution and owner-scoping behave identically to a direct REST call. Operational and demo endpoints (health, echo) are excluded from the MCP surface through route maps. No database changes, no new business logic — this is a thin, contract-derived access layer over existing endpoints.

## Technical Context

**Language/Version**: Python 3.14 (`>=3.14,<3.15`)

**Primary Dependencies**: FastAPI via `aidial-sdk` `DIALApp` (0.37.0), `fastmcp` (new — OpenAPI→MCP generation + ASGI mounting), `httpx` (0.28.1, backing client + event hooks), Pydantic v2 / pydantic-settings (2.14.1)

**Storage**: PostgreSQL via existing SQLAlchemy/SQLModel layer — **no new tables, no migration** (feature adds no persisted state)

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`), `pytest-mock`; in-process MCP client against the mounted ASGI app for integration tests

**Target Platform**: Linux server (Docker), API service only — the background worker is untouched

**Project Type**: Web service (single API service); MCP surface mounted as a sub-app of the existing API

**Performance Goals**: MCP tool calls add no material overhead beyond the equivalent REST call; backing transport is in-process (ASGI), so no extra network hop. Reuse existing `ApiHttpSettings` timeout/connection limits.

**Constraints**: Must not alter existing REST endpoint behavior, contract, or availability (FR-012); credential must never be logged or echoed (FR-011); MCP surface must be derivable from the OpenAPI contract without a separately maintained tool list (FR-002); reuse existing DIAL identity resolution (no new auth scheme).

**Scale/Scope**: 8 exposed operations across 2 routers (`schedules`, `runs`); single-process mount; multi-tenant by `Api-Key`-resolved identity.

## Constitution Check

*GATE: evaluated against Constitution v1.2.0. Re-checked after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Reliability First | Error handling, timeouts, no event-loop blocking, no data loss | **PASS** — async throughout; backing client reuses configured `httpx` timeouts/limits; tool errors map from existing HTTP status codes; no new failure modes introduced (read/lifecycle ops only). |
| II. Observability (NON-NEGOTIABLE) | Structured logging via `src.lib.utils.logging`; DIALApp telemetry; no `print()` | **PASS** — MCP mount/startup and per-tool invocation logged via `logger`; mounted under the same `DIALApp` so OTLP tracing/metrics continue to apply. No new scheduling decisions to log (no enqueue/dequeue paths added). |
| III. Test-First (NON-NEGOTIABLE) | TDD; unit + integration + contract tests; `uv run pytest` green | **PASS (planned)** — unit tests for the header-forwarding hook and route-map exclusion; integration test driving the mounted MCP app end-to-end with a stubbed DIAL identity; contract test asserting tool inventory matches the OpenAPI operation set. All written failing first. |
| IV. Contract-Driven API | Versioned contract for every external interface | **PASS** — the MCP surface is *generated from* the existing FastAPI OpenAPI contract; tool inventory captured in `contracts/`. No ad-hoc dict passing across boundaries; backing calls reuse the published REST contract. |
| V. Simplicity & YAGNI | Simplest viable solution; ≤3 params; no obscuring magic | **PASS** — one generation call + one event hook + one mount; no new abstraction layer; functions grouped with Pydantic settings where needed. |

**Technical-constraints check**: FastAPI app state for reusable components (backing client held in app state, not a module global) ✅; `async`/`await` for all I/O ✅; no `assert` in production ✅; no `.env`/secrets read ✅; new dependency `fastmcp` will be added via `uv` with lockfile committed ✅; `mypy`/`ruff` gates apply ✅.

**Layer rules check**: New code lives under `src/api/` (the API service entry point) and depends only on `src/lib/`. `src/lib/` is not modified to import from `api/`. Dependency arrow flows inward only ✅.

**Result**: No violations. Complexity Tracking table not required.

## Project Structure

### Documentation (this feature)

```text
specs/016-mcp-endpoint-exposure/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── mcp-tools.md      # Generated MCP tool inventory + Api-Key header contract
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── app.py                     # MODIFIED: build + mount MCP sub-app; thread MCP app lifespan into DIALApp lifespan
│   ├── mcp/                       # NEW: MCP exposure package (API-service-local)
│   │   ├── server.py              # build_mcp_server(): FastMCP.from_openapi(openapi, backing_client, route_maps); returns FastMCP
│   │   ├── backing_client.py      # create_backing_client(app): in-process httpx.AsyncClient (ASGITransport) with Api-Key forwarding event hook
│   │   ├── headers.py             # forward_api_key hook using get_http_headers(); route-map / exclusion config helpers
│   │   └── route_maps.py          # RouteMap config: include schedules+runs tags as TOOL, EXCLUDE everything else (health, echo)
│   ├── dependencies/auth.py       # UNCHANGED — reused as-is via the forwarded Api-Key
│   └── v1/endpoints/              # UNCHANGED — schedules.py, runs.py remain the source of truth
└── lib/
    ├── settings.py                # MODIFIED: add mcp_enabled (bool) and mcp_path (str) settings
    └── constants.py               # MODIFIED (if needed): MCP mount path / server name constants

tests/
├── api/
│   └── mcp/                       # NEW
│       ├── test_headers.py        # unit: Api-Key forwarding hook (present / missing / case-insensitive)
│       ├── test_route_maps.py     # unit: schedules+runs → tools; health/echo excluded
│       └── test_server.py         # unit: build_mcp_server produces expected tool inventory
└── integration/
    └── test_mcp_endpoint.py       # integration: in-process MCP client lists tools + invokes one read op with forwarded Api-Key
```

**Structure Decision**: Single web-service layout (constitution `src/api` + `src/lib` split). All new code is API-service-local under `src/api/mcp/`, consistent with the layer rule that `api/` depends on `lib/` and never the reverse. The MCP server is mounted as a sub-application of the existing `DIALApp` rather than run as a separate service, keeping a single deployable, single OpenAPI source of truth, and shared telemetry. No `worker/` or `lib/` business logic changes.

## Complexity Tracking

> No constitution violations identified. Table intentionally empty.

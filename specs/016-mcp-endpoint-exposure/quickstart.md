# Quickstart & Validation: MCP Endpoint Exposure

This guide proves the feature works end-to-end. It validates tool discovery, credential propagation, owner-scoping, and exclusion of operational/demo endpoints. Implementation details live in `tasks.md`; schema details live in `contracts/mcp-tools.md` and `data-model.md`.

## Prerequisites

- `uv sync` completed (including the new `fastmcp` dependency).
- A reachable DIAL instance (or a test stub) for identity resolution, configured via `DIAL_URL` / `DIAL_API_KEY`.
- A PostgreSQL database reachable per `DatabaseSettings`, migrations applied: `uv run alembic upgrade head`.
- MCP surface enabled: `MCP_ENABLED=true` (default), mount path `MCP_PATH=/mcp` (default).

## Run the service

```bash
uv run python -c "from src.api.app import run_api; run_api()"
# MCP endpoint is then mounted at  http://localhost:5000/mcp
# Existing REST API remains at      http://localhost:5000/v1/...
```

## Validation scenarios

### V1 — Tool discovery matches the contract (SC-001, SC-005)

Connect any MCP client to `http://localhost:5000/mcp` and list tools.

- **Expected**: exactly the 8 tools in `contracts/mcp-tools.md` (create/update/get/list schedule, pause, resume, list/get runs).
- **Expected**: no health-check tool, no `echo` tools.

### V2 — Credential propagation & a real operation (SC-002)

With a valid `Api-Key` header on the MCP connection, invoke `list_schedules`.

- **Expected**: returns the same payload shape as `GET /v1/schedules/` for that identity.
- Cross-check: call `GET /v1/schedules/` directly with the same `Api-Key`; results MUST be equivalent.

### V3 — Owner scoping (SC-004)

Invoke `list_schedules` with user A's `Api-Key`, then with user B's `Api-Key`.

- **Expected**: each response contains only that user's schedules; 0 cross-tenant records.

### V4 — Missing / invalid credential is rejected (SC-003)

Invoke any tool (a) with no `Api-Key` header and (b) with an unresolvable `Api-Key`.

- **Expected**: authentication failure surfaced as a tool error; no schedule created/modified. Verify via a follow-up `list_schedules` that state is unchanged.

### V5 — Credential is never leaked (SC-006)

Run V2–V4 with logging at INFO.

- **Expected**: grep the logs and tool responses for the credential value → 0 matches.

### V6 — REST API unaffected (FR-012)

Exercise the existing REST endpoints (create/list/get/pause/resume schedule; list/get runs) directly.

- **Expected**: identical behavior and contract to before the feature.

## Automated test entry points

| Test | Asserts |
|------|---------|
| `tests/api/mcp/test_route_maps.py` | schedules+runs → tools; health/echo excluded (V1, SC-005) |
| `tests/api/mcp/test_headers.py` | `Api-Key` forwarding hook: present / missing / case-insensitive (V2, V4) |
| `tests/api/mcp/test_server.py` | generated tool inventory equals the 8-tool contract (V1, SC-001) |
| `tests/integration/test_mcp_endpoint.py` | in-process MCP client lists tools + invokes a read op with forwarded `Api-Key`, DIAL stubbed (V1–V4) |

## Quality gates (must pass)

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src
```

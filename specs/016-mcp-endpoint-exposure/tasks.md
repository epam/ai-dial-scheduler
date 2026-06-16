---

description: "Task list for MCP Endpoint Exposure"
---

# Tasks: MCP Endpoint Exposure

**Input**: Design documents from `/specs/016-mcp-endpoint-exposure/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/mcp-tools.md, quickstart.md

**Tests**: INCLUDED — Constitution v1.2.0 Principle III (Test-First) is NON-NEGOTIABLE. All test tasks MUST be written and confirmed FAILING before the implementation tasks in the same phase.

**Organization**: Tasks are grouped by user story. US1 and US2 are both P1 (MVP = US1 + US2); US3 is P2.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 / US2 / US3 (maps to spec.md user stories)
- All paths are repository-relative.

## Path Conventions

Single web-service project (Constitution `src/api` + `src/lib` split). New code is API-service-local under `src/api/mcp/`. Tests mirror under `tests/`. All `__init__.py` files MUST be empty (project rule).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the dependency, configuration, and package skeleton needed by every story.

- [X] T001 Add `fastmcp` to `[project.dependencies]` via `uv add fastmcp`, run `uv sync`, and commit the updated `uv.lock`; confirm clean resolution on the Python 3.14 pin (research R7) — `pyproject.toml`, `uv.lock`
- [X] T002 [P] Add `mcp_enabled: bool = True` and `mcp_path: str = "/mcp"` settings to the `Settings` class in `src/lib/settings.py`
- [X] T003 [P] Add MCP constants (server name e.g. `MCP_SERVER_NAME`, internal backing base URL e.g. `MCP_BACKING_BASE_URL = "http://scheduler.internal"`) in `src/lib/constants.py`
- [X] T004 Create empty package markers: `src/api/mcp/__init__.py`, `tests/api/mcp/__init__.py`, and `tests/integration/__init__.py` if missing (files MUST be empty)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Backing transport, server-build scaffold, and mounting that ALL user stories build on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T005 Implement `create_backing_client(app, event_hooks=None)` in `src/api/mcp/backing_client.py` — an in-process `httpx.AsyncClient` using `httpx.ASGITransport(app=app)`, `base_url=MCP_BACKING_BASE_URL`, reusing `ApiHttpSettings` timeouts/limits; accepts optional request `event_hooks` (async/await only)
- [X] T006 Implement `build_mcp_server(app, settings)` scaffold in `src/api/mcp/server.py` — calls `FastMCP.from_openapi(openapi_spec=app.openapi(), client=<backing client>, name=MCP_SERVER_NAME, route_maps=<placeholder>)` and returns the `FastMCP` instance
- [X] T007 Mount the MCP sub-app in `src/api/app.py` guarded by `settings.mcp_enabled`: build `mcp_app = mcp.http_app(path="/")`, thread `mcp_app.lifespan` into the existing `DIALApp` `lifespan`, then `app.mount(settings.mcp_path, mcp_app)`; emit a structured startup log via `logger` (no header values)

**Checkpoint**: App boots with an (empty-policy) MCP server mounted at `/mcp` and the existing REST API unchanged.

---

## Phase 3: User Story 1 - Operate schedules from an MCP-capable assistant (Priority: P1) 🎯 MVP

**Goal**: Every in-scope schedule/run operation is discoverable and invokable as an MCP tool through the mounted endpoint.

**Independent Test**: Connect an in-process MCP client to the mounted app, list tools, confirm the 8-tool inventory from `contracts/mcp-tools.md`, and invoke a read op (identity stubbed) returning a REST-equivalent payload.

### Tests for User Story 1 (write first, confirm FAIL) ⚠️

- [X] T008 [P] [US1] Integration test: in-process MCP client connects to the mounted app and lists tools; assert the 8 expected tools (create/update/get/list schedule, pause, resume, list/get runs) are present — `tests/unit/api/mcp/test_mcp_tools.py`
- [X] T009 [P] [US1] Integration test: invoke the `list_schedules` tool with the `get_user_id` dependency overridden (stubbed identity); assert the result shape matches `GET /v1/schedules/` — `tests/unit/api/mcp/test_mcp_tools.py`

### Implementation for User Story 1

- [X] T010 [US1] Finalize `build_mcp_server` so generation from `app.openapi()` produces tools for all `schedules` + `runs` operations — `src/api/mcp/server.py`
- [X] T011 [US1] Ensure mount wiring exposes the generated tools at `settings.mcp_path`; log the registered tool names at startup via `logger` (names only, never header/credential values) — `src/api/app.py`, `src/api/mcp/server.py`

**Checkpoint**: Tools are discoverable and invokable end-to-end through `/mcp` (auth correctness delivered in US2).

---

## Phase 4: User Story 2 - Caller identity is preserved through MCP (Priority: P1) 🎯 MVP

**Goal**: Each tool call's `Api-Key` is forwarded to the underlying endpoint so identity resolution and owner-scoping behave exactly like a direct REST call; credential-less/unresolvable calls are rejected.

**Independent Test**: Unit-test the forwarding hook (present/missing/case-insensitive); end-to-end, confirm two users see only their own data, and a missing/invalid key is rejected with no state change.

### Tests for User Story 2 (write first, confirm FAIL) ⚠️

- [X] T012 [P] [US2] Unit tests for `forward_api_key` hook: injects `Api-Key` when present (case-insensitive lookup via lower-cased header name), no injection when absent, never mutates other headers — `tests/unit/api/mcp/test_headers.py`
- [X] T013 [P] [US2] Integration tests: (a) tool call with no `Api-Key` is rejected as an auth error with zero scheduling-state change; (b) valid key returns owner-scoped data; (c) two different keys yield isolated results (DIAL identity stubbed per key) — `tests/unit/api/mcp/test_mcp_tools.py`

### Implementation for User Story 2

- [X] T014 [US2] Implement the `forward_api_key` async request event hook in `src/api/mcp/headers.py` using `get_http_headers()` (from `fastmcp.server.dependencies`) and `API_KEY_HEADER`; read `incoming.get(API_KEY_HEADER.lower())` and inject onto the outgoing request — MUST NOT log the value
- [X] T015 [US2] Wire `forward_api_key` into the backing client's request `event_hooks` where `build_mcp_server` constructs the client — `src/api/mcp/server.py`, `src/api/mcp/backing_client.py`
- [X] T016 [US2] Verify the installed FastMCP `get_http_headers()` does not strip `Api-Key`; if it does, switch to `get_http_headers(include={"api-key"})` and document the version behavior — `src/api/mcp/headers.py`

**Checkpoint**: MVP complete — tools are discoverable, invokable, identity-scoped, and credential-safe.

---

## Phase 5: User Story 3 - MCP surface stays in sync with the API contract (Priority: P2)

**Goal**: The exposed tool set is derived from the live OpenAPI contract and excludes operational/demo endpoints, with no separately maintained tool list.

**Independent Test**: Unit-test that route maps send `schedules`+`runs` to TOOL and exclude health/echo, and that the generated inventory equals the 8-tool contract with excluded endpoints absent.

### Tests for User Story 3 (write first, confirm FAIL) ⚠️

- [X] T017 [P] [US3] Unit test for route maps: `schedules`/`runs` tags → `MCPType.TOOL`; health-check and `echo` routes → `MCPType.EXCLUDE` — `tests/unit/api/mcp/test_route_maps.py`
- [X] T018 [P] [US3] Unit test for `build_mcp_server` inventory: tool set equals the 8-tool contract from `contracts/mcp-tools.md`; no health/echo tools present (with `echo_endpoints` toggled both on and off) — `tests/unit/api/mcp/test_server.py`

### Implementation for User Story 3

- [X] T019 [US3] Implement `route_maps` in `src/api/mcp/route_maps.py`: ordered list — `RouteMap(tags={"schedules"}, mcp_type=MCPType.TOOL)`, `RouteMap(tags={"runs"}, mcp_type=MCPType.TOOL)`, then catch-all `RouteMap(mcp_type=MCPType.EXCLUDE)`
- [X] T020 [US3] Apply `route_maps` in `build_mcp_server` and confirm generation reads the live `app.openapi()` (no hand-maintained tool list), so contract changes flow through automatically — `src/api/mcp/server.py`

**Checkpoint**: Surface is contract-derived and correctly scoped; all three stories functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validation, quality gates, and credential-safety confirmation across stories.

- [X] T021 [P] Add a log-safety assertion test confirming `Api-Key` value appears in zero log records during a tool call (SC-006) — `tests/unit/api/mcp/test_headers.py`
- [ ] T022 [P] Update documentation to note the MCP endpoint mounted at `settings.mcp_path` and the `Api-Key` requirement — `README.md`
- [ ] T023 Run quickstart validation scenarios V1–V6 — `specs/016-mcp-endpoint-exposure/quickstart.md`
- [X] T024 Run all quality gates and fix issues: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --cov=src`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — start immediately.
- **Foundational (Phase 2)**: depends on Setup — BLOCKS all user stories.
- **User Stories (Phase 3–5)**: all depend on Foundational.
  - US1 (P1) and US2 (P1) together form the MVP.
  - US3 (P2) follows; can also proceed in parallel once Foundational is done.
- **Polish (Phase 6)**: depends on the desired user stories being complete.

### User Story Dependencies

- **US1 (P1)**: starts after Foundational. Delivers discovery + invocation plumbing; independently testable via tool listing and a stubbed-identity invocation.
- **US2 (P1)**: starts after Foundational. Independently testable (hook unit tests + scoping). Note: US1's *authenticated* correctness in production relies on US2's forwarding, but each story's tests stand alone.
- **US3 (P2)**: starts after Foundational. Independent of US1/US2 — touches `route_maps.py` and generation policy only.

### Within Each User Story

- Tests written and FAILING before implementation (TDD, Principle III).
- Backing client/server scaffold (Foundational) before story-specific behavior.
- Story complete before moving to next priority.

### Parallel Opportunities

- Setup: T002, T003 in parallel (different files); T001 first (others assume the dep), T004 independent.
- Tests within a story marked [P] run in parallel (distinct test files / distinct cases).
- US3 (route maps + generation policy) can be developed in parallel with US2 by a second developer — different files (`route_maps.py`/`test_route_maps.py`/`test_server.py` vs `headers.py`/`test_headers.py`).

---

## Parallel Example: MVP (US1 + US2)

```bash
# After Foundational (Phase 2) is complete, launch story tests in parallel:
Task: "US1 integration: list tools, assert 8-tool inventory in tests/integration/test_mcp_endpoint.py"
Task: "US2 unit: forward_api_key present/missing/case-insensitive in tests/api/mcp/test_headers.py"
Task: "US3 unit: route maps exclude health/echo in tests/api/mcp/test_route_maps.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 — both P1)

1. Complete Phase 1 (Setup) and Phase 2 (Foundational).
2. Complete Phase 3 (US1) → tools discoverable and invokable.
3. Complete Phase 4 (US2) → identity forwarded, scoping correct, credential safe.
4. **STOP and VALIDATE**: run quickstart V1–V5; demo the MVP.

### Incremental Delivery

1. Setup + Foundational → MCP mounted (no tools yet).
2. US1 → discovery + invocation (MVP part 1).
3. US2 → identity-scoped, credential-safe (MVP complete).
4. US3 → contract-sync + exclusion hardening.
5. Polish → gates, docs, log-safety, quickstart.

---

## Notes

- [P] = different files, no incomplete-task dependencies.
- All `__init__.py` files MUST remain empty (project rule).
- `Api-Key` value MUST NEVER be logged or echoed (FR-011, SC-006).
- No DB migration — feature adds no persisted state.
- Highest-uncertainty items (validate early): `fastmcp` resolution on Python 3.14 (T001) and `get_http_headers()` not stripping `Api-Key` (T016).
- Commit after each task or logical group.

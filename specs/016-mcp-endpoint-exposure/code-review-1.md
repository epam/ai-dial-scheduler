# Code Review Report — MCP Endpoint Exposure

- **Spec dir**: `specs/016-mcp-endpoint-exposure/`
- **Date**: 2026-06-16
- **Run mode**: Fresh Review
- **Artifacts consulted**: `spec.md`, `plan.md`, `research.md`, `tasks.md`, `.specify/memory/constitution.md` (v1.2.0)
- **Reviewer scope**: New `src/api/mcp/` package + modifications to `src/api/app.py`, `src/lib/constants.py`, `src/lib/settings.py`, and the four new test modules under `tests/unit/api/mcp/`.

## Overall Verdict: NEEDS_CHANGES

The implementation is clean, well-typed, faithful to the plan/research design, and the credential-handling and layer rules are correct. However there is one **MAJOR** reliability defect (the backing `httpx.AsyncClient` is never closed) and one **MAJOR** correctness defect in the lifespan wiring (the original lifespan is captured by reference rather than the wrapped one, producing a fragile double-reference). Both must be fixed before merge. The remaining items are MINOR/SUGGESTION.

Issue counts: 0 BLOCKER, 2 MAJOR, 4 MINOR, 3 SUGGESTION.

---

## Issues

### M1 — Backing httpx client is never closed (resource leak) — `MAJOR` — Status: Open

**Files**: `src/api/mcp/backing_client.py:18-22`, `src/api/mcp/server.py:21-31`, `src/api/app.py:95-102`

`create_backing_client()` documents *"The caller is responsible for closing the returned client."* No caller ever closes it. `build_mcp_server()` creates the client and hands it to `FastMCP.from_openapi(client=...)`, then returns only the `FastMCP` instance — the client reference is lost. In `create_api()` the server is built and mounted, but nothing calls `await backing_client.aclose()` on shutdown.

This violates Constitution Principle I (Reliability First — proper resource lifecycle) and contradicts the plan, which states reusable components are *"held in app state, not a module global"* and that the backing transport reuses configured limits/timeouts with a managed lifecycle. The existing app-level `app_client` (`app.py:31-47`) is correctly managed with `async with`; the MCP backing client is not held anywhere and is not closed.

While `ASGITransport` has no sockets to leak, the `httpx.AsyncClient` still holds a connection pool and is an `AsyncClient` that should be explicitly closed; relying on GC for an async resource is exactly the hazard this principle targets.

**Suggested fix**: Return the client (or store it in `app.state`) so its lifecycle is owned. For example, have `build_mcp_server` return both the server and client, store the client in `app.state.mcp_backing_client`, and close it in the lifespan `finally`:

```python
# app.py, inside lifespan finally (or the combined lifespan)
finally:
    mcp_client = getattr(app.state, "mcp_backing_client", None)
    if mcp_client is not None:
        await mcp_client.aclose()
    scheduler.shutdown(wait=False)
    await database.dispose()
```

Or thread it through the MCP ASGI lifespan in `_wrap_with_mcp_lifespan`. Either way, the client must be explicitly closed on shutdown.

---

### M2 — `_wrap_with_mcp_lifespan` re-wraps a reference to the module-level `lifespan`, not the app's current lifespan — `MAJOR` — Status: Open

**File**: `src/api/app.py:95-101`

```python
mcp_asgi = mcp_server.http_app(path="/")
app.router.lifespan_context = _wrap_with_mcp_lifespan(lifespan, mcp_asgi)
app.mount(settings.mcp_path, mcp_asgi)
```

The wrapper is passed the module-global `lifespan` function directly (`_wrap_with_mcp_lifespan(lifespan, ...)`) and then assigned to `app.router.lifespan_context`, while the app was already constructed with `lifespan=lifespan` (line 74). This works only because the two references happen to be the same object. It is fragile:

- It bypasses whatever `DIALApp.__init__` did with the `lifespan=` argument (DIALApp may wrap/adapt the passed lifespan for telemetry). Re-assigning `app.router.lifespan_context` to a freshly-wrapped *raw* `lifespan` can silently drop any DIALApp-applied wrapping.
- It hardcodes the assumption that the app's lifespan is exactly the module-level `lifespan`, which couples this block to construction order and makes future refactors error-prone.

**Suggested fix**: Capture the lifespan the app actually has and wrap that, rather than the module global:

```python
original = app.router.lifespan_context
app.router.lifespan_context = _wrap_with_mcp_lifespan(original, mcp_asgi)
```

This composes with whatever the framework installed. Additionally, please confirm against the installed `aidial_sdk`/Starlette versions that reassigning `lifespan_context` after `DIALApp` construction is the supported mounting approach (research R5 calls this out as the known "lifespan gotcha"); an integration test that issues a real MCP request through the mounted app would lock this behavior down. (The `test_mcp_endpoint.py` integration test referenced in the plan/tasks is not present in the reviewed set — see MIN1.)

---

### MIN1 — Planned integration test for the mounted app is absent — `MINOR` — Status: Open

**File**: (expected) `tests/integration/test_mcp_endpoint.py`

`plan.md` (lines 87-88) and the Constitution's Project SDLC require an integration test that drives the mounted MCP app end-to-end (lists tools + invokes one read op with forwarded `Api-Key`). The four reviewed unit tests exercise `build_mcp_server` against a FastMCP `Client` directly, but **none exercise the actual mount + combined lifespan in `app.py`** (`create_api`, `_wrap_with_mcp_lifespan`, `app.mount`). M2's risk is precisely the kind of thing that only a mount-level test catches. Constitution Test-First mandates integration coverage for this interaction.

**Suggested fix**: Add an integration test that builds the real app via `create_api()` (with `mcp_enabled=True`), drives an MCP request through the mounted path, and asserts a forwarded `Api-Key` reaches the endpoint and a missing one is rejected.

---

### MIN2 — `app` parameter typed as `Any` where a narrower type is available — `MINOR` — Status: Open

**Files**: `src/api/mcp/backing_client.py:14`, `src/api/mcp/server.py:13`

`create_backing_client(app: Any, ...)` and `build_mcp_server(app: Any, ...)` use `Any` for the app. The plan and call sites only ever pass an ASGI application (`DIALApp` / `fastapi.FastAPI`). The spec/constitution require complete, specific type hints and discourage implicit/needless `Any` (Typing checklist; Constitution Technical Constraints).

`httpx.ASGITransport` accepts `ASGIApplication`; `app.openapi()` is needed in `server.py`. A reasonable narrowing is `fastapi.FastAPI` (which `DIALApp` subclasses and which provides `.openapi()`), or `starlette.types.ASGIApp` for the transport-only case in `backing_client.py`.

**Suggested fix**: `def create_backing_client(app: ASGIApp, ...)` and `def build_mcp_server(app: FastAPI, settings: Settings) -> FastMCP:`. Verify `mypy` still passes.

---

### MIN3 — `run_api` uses bare `dict` annotation — `MINOR` — Status: Open (pre-existing, in touched file)

**File**: `src/api/app.py:109`

`options: dict = {...}` — untyped `dict`. Not introduced by this feature, but it sits in a modified file and is flagged for completeness against the typing convention. A `dict[str, Any]` would be more precise. Low priority; mention only.

---

### MIN4 — No explicit log/observability on MCP tool invocation — `MINOR` — Status: Open

**Files**: `src/api/mcp/server.py:32`, `src/api/mcp/headers.py`

`build_mcp_server` logs `mcp_server_built` and `app.py` logs `mcp_mounted`, which is good. The plan's Observability section (II) says *"per-tool invocation logged via `logger`."* There is currently no per-invocation log (e.g., tool name, decision) on the forwarding path. The constitution's strict per-decision logging applies to *scheduling decisions* (enqueue/dequeue/etc.), and this feature adds none, so this is not a hard violation — but the plan promised invocation logging. If retained as-is, note the deviation; if added, log **only** the tool/route name and never the `Api-Key` (the current redaction posture is correct — see "Security" below).

**Suggested fix**: Either add a metadata-only invocation log (tool/route name, no header value) or update the plan to drop the per-invocation logging claim. Confirm intent.

---

### S1 — `event_hooks or {}` default-arg pattern — `SUGGESTION`

**File**: `src/api/mcp/backing_client.py:38`

`event_hooks=event_hooks or {}` is fine, but if `event_hooks={}` (falsy) were ever passed intentionally it collapses to the same result — harmless here. Optional: use `event_hooks if event_hooks is not None else {}` for explicitness. Cosmetic.

---

### S2 — `AsyncRequestHook` type alias is single-use — `SUGGESTION`

**File**: `src/api/mcp/backing_client.py:10`

The `type AsyncRequestHook = ...` alias is used once. It aids readability of the signature, which is reasonable, but borders on YAGNI given a single use. Keep if you find it clarifying; otherwise inline. No action required.

---

### S3 — `EXPECTED_TOOL_NAMES` duplicated across three test modules — `SUGGESTION`

**Files**: `tests/unit/api/mcp/test_headers.py:11`, `test_server.py:11`, `test_mcp_tools.py:19`

The same 8-name set is defined three times (and is unused in `test_headers.py` — see S note below). Consider a shared `conftest.py` fixture/constant to keep the contract assertion DRY. Minor test hygiene.

Additionally, in `test_headers.py` the `EXPECTED_TOOL_NAMES` constant (lines 11-20) is **dead code** — never referenced in that module. Remove it.

---

## Intent Alignment

The implementation matches the design recorded in `research.md` and `plan.md` closely and correctly:

- **FR-002 / SC-007 (contract-derived surface)**: `build_mcp_server` generates from `app.openapi()` via `FastMCP.from_openapi`; no hand-authored tool list. ✅
- **FR-004 / FR-005 (Api-Key propagation)**: `forward_api_key` reads `get_http_headers()` (lower-cased) and injects `API_KEY_HEADER` onto the outgoing request via an httpx request event hook, exactly per research R3. Edge cases (missing key, empty headers) are handled by the `if api_key:` guard and no-op. ✅
- **FR-009 / SC-005 (exclude health/echo)**: `ROUTE_MAPS` allow-lists `schedules`/`runs` tags and excludes everything else via a trailing catch-all, per research R2. Tests confirm health/echo/unknown tags are excluded. ✅
- **FR-011 / SC-006 (credential never logged)**: `forward_api_key` logs nothing; `build_mcp_server`/`app.py` log only metadata (server name, mount path). `test_api_key_value_not_logged` guards this. ✅
- **FR-012 (REST unchanged)**: MCP is mounted behind an `mcp_enabled` toggle; REST routers are untouched. ✅
- **Layer rules**: All new code lives under `src/api/mcp/` and imports only from `src/lib/*` and third-party packages. No `src/lib/` module imports from `src/api/`. ✅

**Deviations from stated intent** (tracked above): missing per-tool invocation logging promised in plan §II (MIN4); missing mount-level integration test promised in plan/tasks (MIN1); backing-client lifecycle not actually managed despite the plan's "held/managed" framing (M1).

One note on `mcp_enabled` default: `settings.py:98` sets `mcp_enabled: bool = True`. Research R6 left the default open ("True or default off per ops preference"). Defaulting **on** means the feature is live by default — acceptable per the spec, but confirm this is the desired rollout posture given M1/M2 are unresolved.

---

## Checklist Pass (areas with no issues found)

- **Security / credential handling**: `Api-Key` is read by name, forwarded, and never logged or echoed. `get_http_headers()` default does not strip `api-key` (verified against research R3 assumption; the explicit-by-name read is the safe approach). No secrets in code. ✅
- **Async correctness**: `forward_api_key` is `async`; the backing client is `httpx.AsyncClient` with ASGITransport — no blocking I/O on the event loop. ✅
- **Project structure & conventions**: `__init__.py` is empty ✅; absolute imports, stdlib→third-party→local ordering ✅; Google-style docstrings on all public functions ✅; no `print()`, no `assert`, no raw SQL ✅; `snake_case`/`PascalCase` respected ✅.
- **Settings/constants**: new settings (`mcp_enabled`, `mcp_path`) and constants (`MCP_SERVER_NAME`, `MCP_BACKING_BASE_URL`) added in the correct `lib/` modules using Pydantic v2 settings. ✅
- **Timeouts/limits**: backing client reuses `ApiHttpSettings` timeout/connection-limit config (Reliability). ✅
- **Test coverage of pure logic**: header forwarding (present/absent/lowercase/non-mutation/no-log), route-map decisions, and tool inventory are all well covered. ✅

---

## Required before merge

- **M1** — own and close the backing `httpx.AsyncClient`.
- **M2** — wrap the app's actual lifespan context, not the module-global `lifespan` reference.

MINOR items (esp. MIN1 integration test) are strongly recommended given the Constitution's Test-First mandate, but are not merge blockers on their own.

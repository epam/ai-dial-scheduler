# Code Review Report (Round 2) — MCP Endpoint Exposure

- **Spec dir**: `specs/016-mcp-endpoint-exposure/`
- **Date**: 2026-06-16
- **Run mode**: Verification (follow-up to `code-review-1.md`)
- **Artifacts consulted**: `code-review-1.md`, `spec.md`, `plan.md`
- **Reviewer scope**: Changed files only — `src/api/app.py`, `src/api/mcp/server.py`, `src/api/mcp/headers.py`, `src/api/mcp/backing_client.py`, and the test modules `tests/unit/api/mcp/test_headers.py`, `test_server.py`, `test_mcp_tools.py`. Re-confirmed `src/api/mcp/route_maps.py`, `src/lib/constants.py`, and `src/api/mcp/__init__.py` (empty) for no regressions.
- **Gates**: `uv run ruff check` and `uv run mypy` on the changed source files — both **green** (6 source files, no issues).

## Overall Verdict: PASS (with one known open MINOR — integration test)

Both round-1 MAJOR defects are resolved correctly, and all four targeted MINOR items (MIN2, MIN3, MIN4) plus suggestion S3 are addressed. No regressions to credential safety, async correctness, layer rules, or project conventions. The only remaining open item is the previously-known MIN1 integration test gap, which is not a merge blocker on its own but is recommended given the Constitution's Test-First mandate. No new issues introduced by the tuple-return change.

Issue status roll-up: **2 MAJOR resolved**, **3 MINOR resolved**, **1 MINOR open (MIN1)**, **1 SUGGESTION resolved (S3)**, **0 new issues**.

---

## Round-1 Issue Verification

### M1 — Backing httpx client is never closed — Status: **RESOLVED**

**Files**: `src/api/mcp/server.py:13,36`, `src/api/app.py:48-53,101-103`

The lifecycle is now owned end-to-end:

- `build_mcp_server` returns `tuple[FastMCP, httpx.AsyncClient]` (`server.py:13`, `return mcp_server, backing_client` at line 36), so the client reference is no longer lost.
- `create_api()` stores it before the lifespan can run: `app.state.mcp_backing_client = mcp_backing_client` (`app.py:103`), set at app-construction time, prior to any request/lifespan execution.
- The `lifespan` `finally` block retrieves and closes it (`app.py:49-51`):

  ```python
  mcp_client = getattr(app.state, "mcp_backing_client", None)
  if mcp_client is not None:
      await mcp_client.aclose()
  ```

**Reachability confirmed**: the `lifespan` receives the `app` instance, and `app.state.mcp_backing_client` is assigned in `create_api()` before `app.router.lifespan_context` is invoked. The `getattr(..., None)` guard correctly handles the `mcp_enabled=False` path where the attribute is never set, so the close is a safe no-op there. The close runs in `finally`, so it executes even if startup partially failed. `aclose()` is correctly awaited. This satisfies Constitution Principle I (resource lifecycle) and the plan's "held in app state, managed lifecycle" framing.

### M2 — Lifespan wrapping re-wrapped the module-global `lifespan` by reference — Status: **RESOLVED**

**File**: `src/api/app.py:104-105`

```python
original_lifespan = app.router.lifespan_context
app.router.lifespan_context = _wrap_with_mcp_lifespan(original_lifespan, mcp_asgi)
```

The wrapper now composes whatever lifespan the app currently has (`app.router.lifespan_context`) rather than the module-level `lifespan` symbol, exactly as recommended in round 1. This removes the fragile double-reference coupling and composes with any wrapping `DIALApp` applied at construction. `_wrap_with_mcp_lifespan` (`app.py:56-65`) correctly nests `async with original_lifespan(app)` then `async with mcp_asgi.lifespan(mcp_asgi)`, so the MCP sub-app's own lifespan is started/stopped within the parent's window. This is the correct ordering: backing client teardown (in the parent `finally`) runs after the MCP ASGI lifespan exits.

**Residual fragility note (not blocking)**: Reassigning `app.router.lifespan_context` after `DIALApp` construction remains the documented "lifespan gotcha" from research R5. The composition is now correct, but the *only* coverage of this exact wiring is still manual reasoning — no test drives `create_api()` + mount + combined lifespan. This is precisely the gap tracked as MIN1; see below.

### MIN1 — Mount-level integration test absent — Status: **OPEN (known, unchanged)**

**File**: (expected) `tests/integration/test_mcp_endpoint.py` — confirmed **still not present**.

As flagged in the task brief, this is a known open item and is not re-escalated. The reviewed unit tests drive `build_mcp_server` through a FastMCP `Client` directly; none exercise `create_api()`, `_wrap_with_mcp_lifespan`, or `app.mount(...)`. With M2 now relying on `app.router.lifespan_context` reassignment, a single integration test that builds the real app with `mcp_enabled=True`, lists tools, invokes one read op with a forwarded `Api-Key`, and asserts a missing key is rejected, would lock down both the mount wiring and the backing-client teardown. Recommended before relying on this in production, but not a hard merge blocker per round-1's disposition.

### MIN2 — `app: Any` narrowed — Status: **RESOLVED**

`create_backing_client(app: FastAPI, ...)` (`backing_client.py:15`) and `build_mcp_server(app: FastAPI, ...)` (`server.py:13`) now use `fastapi.FastAPI`. `mypy` passes, confirming `.openapi()` and `ASGITransport(app=...)` accept the narrowed type. Correct.

### MIN3 — `run_api` bare `dict` — Status: **RESOLVED**

`options: dict[str, Any] = {...}` (`app.py:114`), with `Any` already imported. Correct.

### MIN4 — Per-invocation observability — Status: **RESOLVED (metadata-only)**

`headers.py:19` now emits:

```python
logger.debug("mcp_api_key_forward", forwarded=bool(api_key), path=str(request.url.path))
```

This satisfies the plan's §II per-invocation logging intent while honoring FR-011/SC-006: it logs only a boolean (whether a key was present) and the request path — **never the key value**. The existing `test_api_key_value_not_logged` (`test_headers.py:65-78`) runs `caplog.at_level("DEBUG")` and asserts the secret is absent from every record, so it now actively guards this new debug line. Verified safe.

### S3 — `EXPECTED_TOOL_NAMES` dead code + tuple call sites — Status: **RESOLVED**

- `EXPECTED_TOOL_NAMES` removed from `tests/unit/api/mcp/test_headers.py` (the module no longer defines or references it — confirmed).
- All `build_mcp_server(...)` call sites unpack the tuple: `test_server.py` (5 sites) and `test_mcp_tools.py` (5 sites) use `mcp_server, _ = build_mcp_server(...)`. Confirmed no remaining single-value assignments.

Note: the same `EXPECTED_TOOL_NAMES` set still appears in both `test_server.py:11` and `test_mcp_tools.py:19`. The round-1 suggestion to DRY this into a shared `conftest.py` constant remains optional cosmetic test hygiene — not raised as a blocker. (S1 and S2 were suggestions in `backing_client.py`; that file is unchanged in those lines, so they remain optional and are not re-listed.)

---

## New Issues

None. The tuple-return-type change is consistently applied across production code and all call sites. The `_ = build_mcp_server(...)` discard in tests is acceptable: those tests assert on tool inventory/invocation and do not need to close the in-process `ASGITransport` client (no real sockets; FastMCP `Client` context manages its own connection). No test leaks a client that needs closing in a way that would affect correctness.

One minor observation (not an issue): tests intentionally discard the backing client with `_`, while the production path owns and closes it via `app.state`. This is the right split — unit tests should not need lifespan management. If a future test asserts teardown behavior, it would belong in the MIN1 integration test, not these unit modules.

---

## Intent Alignment

All previously-aligned requirements continue to hold and none regressed:

- **FR-002 / SC-007** (contract-derived surface): `FastMCP.from_openapi(openapi_spec=app.openapi(), ...)` unchanged. Aligned.
- **FR-004 / FR-005** (Api-Key propagation): `forward_api_key` reads `get_http_headers()` (lower-cased lookup) and injects `API_KEY_HEADER` via the request event hook. Aligned.
- **FR-009 / SC-005** (exclude health/echo): `ROUTE_MAPS` unchanged — schedules/runs as TOOL, trailing EXCLUDE catch-all. Aligned.
- **FR-011 / SC-006** (credential never logged): the *new* debug log records only `forwarded` (bool) and `path`; `test_api_key_value_not_logged` enforces it. Aligned and now better covered.
- **FR-012** (REST unchanged): MCP still behind `mcp_enabled`; REST routers untouched. Aligned.
- **Plan §I/§II**: backing-client lifecycle now genuinely managed in app state (closes M1's prior deviation); per-invocation logging now present (closes MIN4's prior deviation).

Remaining divergence: the integration test promised in plan/tasks (§III, lines 87-88) is still absent — tracked as MIN1.

---

## Checklist Pass (re-confirmed, no regressions)

- **Security / credential handling**: Api-Key read by name, forwarded, never logged or echoed; new debug log is metadata-only. PASS.
- **Async correctness**: `forward_api_key` async; `aclose()` awaited in `finally`; backing client is `httpx.AsyncClient` over ASGITransport — no event-loop blocking. PASS.
- **Type safety**: `app: FastAPI`, `options: dict[str, Any]`, tuple return typed `tuple[FastMCP, httpx.AsyncClient]`; `mypy` green. PASS.
- **Project structure & conventions**: `src/api/mcp/__init__.py` empty; absolute imports; Google-style docstrings; no `print()`, no `assert`, no raw SQL; `snake_case`/`PascalCase`. PASS.
- **Resource lifecycle**: MCP backing client owned in `app.state` and closed on shutdown; `getattr` guard handles disabled path. PASS.
- **Lint/format/type gates**: `ruff check` and `mypy` pass on all changed files. PASS.

---

## Merge Recommendation

**APPROVE for merge.** All round-1 MAJOR and targeted MINOR items are resolved with no regressions, and `ruff`/`mypy` are green. The single remaining open item (MIN1, mount-level integration test) is a known, accepted gap; strongly recommend adding it as a fast follow given M2 relies on post-construction `lifespan_context` reassignment that currently has no automated coverage. If team policy requires Test-First integration coverage before merge, treat MIN1 as the one remaining prerequisite.

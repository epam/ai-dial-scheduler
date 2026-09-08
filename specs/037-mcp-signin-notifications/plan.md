# Implementation Plan: Scheduler MCP Sign-In Status Signal

**Branch**: `037-mcp-signin-notifications` | **Date**: 2026-08-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/037-mcp-signin-notifications/spec.md`

## Summary

Give the assistant a structured, machine-readable way to know whether the current user is
signed in to an external service before creating or running a schedule against it, and make
creating/updating a schedule fail closed (no side effects) when they are not. Source of truth
for "signed in" is `GET /v1/applications/{scheduler_app_id}` — DIAL Core's own application
status endpoint — read via `DialClient.get_external_service_auth_status`, authenticated with
**the caller's own forwarded `Api-Key`** so the returned `user_level_auth_status`/
`app_level_auth_status` reflect that same caller. Add a `SignInService` in `src/lib/services`
that wraps this probe and returns a typed
`SignInStatus` (`signed_in` / `not_signed_in` / `unknown`), expose it read-only via
`GET /v1/sign-in/status` (tagged `sign-in`, auto-published as the MCP tool
`get_sign_in_status`), and enforce it as a precondition inside the existing create/update
schedule handlers in `src/api/v1/endpoints/schedules.py`, rejecting with 403 (`not_signed_in`)
or 502 (`unknown`) before any repository write. Per FR-011/FR-012, when the caller forwards a
client-channel id (`X-DIAL-CLIENT-CHANNEL-ID`), a not-signed-in result also pushes an
`external-service/signin` popup to that channel via DIAL Core's existing
`POST /v1/ops/client-channel/interact` — using the caller's own forwarded identity, since Core
resolves the channel by the caller's bucket — and re-checks once on success. This mechanism is
implemented and confirmed working end-to-end against a live local DIAL Core + Keycloak stack;
this feature does not modify DIAL Core or ai-dial-chat, only calls their existing protocol.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, Pydantic v2, httpx, FastMCP (existing `DialClient`, no new
third-party dependency)

**Storage**: N/A — sign-in status is computed live against DIAL on every call; nothing is
persisted by this feature

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server (API service; the background worker is untouched by this
feature)

**Project Type**: Web service (`src/api`, shared `src/lib`); no worker changes

**Performance Goals**: One additional DIAL application-status call per sign-in status check and
per create/update request — same cost class as the OBO call the worker already makes per run
(feature 026); no caching (mirrors that precedent)

**Constraints**: Never log or return any issued secret (only the boolean/enum outcome and
DIAL's own status text may surface); async I/O throughout; `mypy`, `ruff` gates must pass; the
response contract (FR-010) must be stable enough for an unbuilt future consumer to rely on

**Scale/Scope**: One new service, one new schema module, one new endpoint module, one new
exception type, one new MCP route-map tag, two call sites added to the existing schedules
endpoint (create, update), one new `DialClient` method + one new MCP header-forwarding hook
for the client-channel popup path

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Reliability First**: PASS. `get_external_service_auth_status` reuses `DialClient`'s
  existing error handling (`DialServiceError` on any non-2xx/transport/malformed-body failure);
  the service maps `app_level_auth_status != "SIGNED_IN"` to `unknown`, `user_level_auth_status
  == "SIGNED_IN"` to `signed_in`, and anything else to `not_signed_in`, and any
  `DialServiceError` to `unknown`, so an outage can never be reported as "not signed in." No
  blocking I/O; the single httpx call is awaited. The client-channel popup call is wrapped in
  its own `asyncio.timeout` (wall-clock, not reset by Core's SSE heartbeats) and a catch-all
  that reports failure as `False` rather than raising — a popup attempt can never turn a clean
  `not_signed_in`/`unknown` result into an unhandled error.
- **II. Observability (NON-NEGOTIABLE)**: PASS. New structured log events
  (`sign_in_check_started`, `sign_in_check_result`) via `src.lib.utils.logging` `logger`, keyed
  by `user_id`/`service_id`/`status`. No secret is ever logged. This feature adds no new
  persisted run/decision records — it is a stateless read plus a precondition on an existing
  write path.
- **III. Test-First (NON-NEGOTIABLE)**: PASS (enforced in `/speckit-implement`). Unit tests:
  `SignInService` state mapping (both-signed-in/user-signed-out/app-not-approved/
  `DialServiceError` → signed_in/not_signed_in/unknown/unknown), the `get_sign_in_status`
  endpoint, the create/update precondition (403 vs 502 vs pass-through), MCP route-map/tool
  exposure. Integration tests: schedule create/update rejected end-to-end when the
  dependency-injected `SignInService` reports `not_signed_in`.
- **IV. Contract-Driven API**: PASS. `SignInStatus` is a versioned Pydantic response model
  (auto-published via OpenAPI, also surfaced through MCP as `get_sign_in_status`); the 403
  rejection on create/update carries a structured `detail` object (service id + reason), not a
  bare string, per FR-003/FR-007/FR-010.
- **V. Simplicity & YAGNI**: PASS. No caching layer, no new persistence, no new DIAL endpoint —
  reuses `DialClient.get_application`'s sibling pattern for the new
  `get_external_service_auth_status` method, `get_dial_client`, and the existing
  `EXTERNAL_SERVICE_URL_TEMPLATE`/`scheduler_app_id` settings. The precondition is a small
  helper function shared by the two existing handlers, not a new abstraction layer.

**Result: PASS — no complexity deviations to track.**

## Project Structure

### Documentation (this feature)

```text
specs/037-mcp-signin-notifications/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md         # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── dependencies/
│   │   └── sign_in.py              # NEW — get_sign_in_service DI provider
│   └── v1/
│       └── endpoints/
│           ├── sign_in.py          # NEW — GET /v1/sign-in/status (tag "sign-in")
│           └── schedules.py        # MODIFIED — precondition check on create/update
│   └── mcp/
│       └── route_maps.py           # MODIFIED — add RouteMap(tags={"sign-in"}, ...)
│   └── mcp/
│       └── headers.py              # MODIFIED — forward_client_channel_id hook
└── lib/
    ├── clients/
    │   └── dial_client.py          # MODIFIED — get_external_service_auth_status(),
    │                               #   request_external_service_signin()
    ├── schemas/
    │   └── sign_in.py              # NEW — SignInStatus response model,
    │                               #   ExternalServiceAuthStatus internal DTO
    ├── services/
    │   └── sign_in_service.py      # NEW — SignInService.check()/.require()
    ├── settings.py                 # MODIFIED — client_channel_signin_timeout_seconds
    ├── constants.py                # MODIFIED — CLIENT_CHANNEL_HEADER, interact path/method
    └── exceptions/
        └── errors.py               # MODIFIED — add SignInRequiredError

tests/
├── unit/
│   ├── api/
│   │   ├── dependencies/test_sign_in.py       # NEW
│   │   └── v1/endpoints/test_sign_in.py       # NEW
│   └── lib/services/test_sign_in_service.py   # NEW
└── integration/
    └── test_sign_in_api.py                    # NEW — + precondition assertions added to
                                                 #   the existing schedules integration tests
```

**Structure Decision**: Single web-service project (`src/api` + shared `src/lib`), matching the
constitution's mandated layout. No worker changes: this feature's application-status check is a
pre-flight check, independent of the run-time OBO-credential fetch the worker already performs
(`DialClient.get_obo_credentials`, unaffected). No new top-level directories are introduced; the
feature slots entirely into the existing `dependencies/`, `endpoints/`, `schemas/`, `services/`,
`exceptions/` folders per the constitution's Project Structure section.

## Complexity Tracking

*No entries — Constitution Check passed without violations.*

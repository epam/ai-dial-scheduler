# Implementation Plan: User Identity Authentication for Schedule Endpoints

**Branch**: `011-user-id-auth` | **Date**: 2026-06-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/011-user-id-auth/spec.md`

## Summary

Replace the hard-coded `MOCK_USER_ID` used by every schedule and schedule-run endpoint with a
real caller identity resolved per request. A new FastAPI dependency `get_user_id` takes the
incoming `Api-Key` header, calls the DIAL service's `GET {dial_url}/v1/info` endpoint, and
extracts a non-empty `user_id` from the response by walking an ordered, configurable list of
candidate paths (`settings.user_id_path_list`, default `["userClaims.mail.0", "project"]`).
Path segments are dict keys; numeric segments are array indices. The first path yielding a
non-empty value wins. Missing/invalid credentials or an unresolvable identity reject the
request; DIAL transport/upstream failures reject it as a bad gateway. The resolved `user_id`
is injected into all six endpoints and forwarded to the service layer in place of the mock.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI (via `aidial_sdk.DIALApp`), httpx 0.28.1 (shared
`app.state.app_client`), Pydantic v2 / pydantic-settings, SQLAlchemy/SQLModel

**Storage**: PostgreSQL (unchanged — no schema change; identity only scopes existing queries)

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock; FastAPI
`TestClient` with `app.dependency_overrides`

**Target Platform**: Linux server (Docker), API service

**Project Type**: Web service (API service in `src/api`, shared logic in `src/lib`)

**Performance Goals**: One additional DIAL `GET /v1/info` round-trip per authenticated request;
reuse the shared keep-alive HTTP client to avoid per-request connection setup

**Constraints**: Async I/O only (no event-loop blocking); no secrets logged; no raw SQL; type
hints + mypy clean; ruff clean (line-length 120); `lib/` must not import from `src.api`

**Scale/Scope**: Six existing endpoints (4 schedule + 2 run); no new endpoints, no new DB tables

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Reliability First**: PASS — DIAL call uses explicit timeout (`settings.http_timeout_seconds`)
  via the shared async client; transport and non-2xx responses are caught and mapped to
  deterministic HTTP errors (401 for rejected/empty identity, 502 for upstream/transport
  failure). No event-loop blocking (async httpx). No data loss risk (read-only auth path).
- **II. Observability**: PASS — auth decisions (rejection reason: missing key / DIAL error /
  no identity resolved) are logged via `src.lib.utils.logging.logger` with reason codes and no
  secret values. The DIAL call inherits DIALApp OTLP tracing.
- **III. Test-First (NON-NEGOTIABLE)**: PASS (planned) — unit tests for the path-extraction
  util, the DIAL client, and the `get_user_id` dependency are written first and confirmed
  failing (Red) before implementation. Existing endpoint unit tests are updated to override
  `get_user_id`. Integration test exercises the wired dependency.
- **IV. Contract-Driven API**: PASS — the DIAL `GET /v1/info` request/response shape and the
  `get_user_id` dependency contract are captured in `contracts/`. Endpoint request/response
  schemas are unchanged; only a `401`/`502` failure mode and an `Api-Key` requirement are added,
  reflected in the OpenAPI via the dependency.
- **V. Simplicity & YAGNI**: PASS — no identity caching, no new persistence, no new abstraction
  beyond a single thin DIAL client + one pure extraction helper + one dependency. Functions stay
  within the 2–3 parameter limit. No Complexity Tracking entries required.

**Result**: PASS — no violations; Complexity Tracking table not required.

## Project Structure

### Documentation (this feature)

```text
specs/011-user-id-auth/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── dial-info.md             # DIAL GET /v1/info request/response contract
│   └── get-user-id-dependency.md # get_user_id dependency behavior contract
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/
├── api/
│   └── dependencies/
│       └── auth.py              # NEW: get_dial_client + get_user_id dependencies; UserIdDep alias
│   └── v1/endpoints/
│       ├── schedules.py         # MODIFIED: inject UserIdDep, drop MOCK_USER_ID (4 routes)
│       └── runs.py              # MODIFIED: inject UserIdDep, drop MOCK_USER_ID (2 routes)
└── lib/
    ├── clients/
    │   └── dial_client.py       # NEW: DialClient.get_user_info(api_key) -> dict
    ├── exceptions/
    │   └── errors.py            # MODIFIED: add DialServiceError, UserIdNotResolvedError
    ├── settings.py              # MODIFIED: add user_id_path_list setting
    └── utils/
        └── user_id.py           # NEW: extract_user_id(data, paths) pure traversal helper

tests/
├── unit/
│   ├── api/
│   │   ├── dependencies/
│   │   │   └── test_auth.py           # NEW: get_user_id dependency (success + all reject paths)
│   │   └── v1/endpoints/
│   │       ├── test_schedules.py      # MODIFIED: override get_user_id; assert resolved id
│   │       └── test_runs.py           # MODIFIED: override get_user_id; assert resolved id
│   └── lib/
│       ├── clients/
│       │   └── test_dial_client.py    # NEW: DialClient success + error mapping
│       ├── utils/
│       │   └── test_user_id.py        # NEW: path traversal / fallback / empty cases
│       └── test_settings.py           # MODIFIED: assert user_id_path_list default
└── integration/
    └── test_user_id_auth.py           # NEW: endpoint scoped by resolved id via mocked DIAL
```

**Structure Decision**: Web service layout per the constitution. The HTTP-facing dependency
lives in `src/api/dependencies/auth.py`; the reusable DIAL client lives in `src/lib/clients/`
and the pure path-extraction logic in `src/lib/utils/` so both are framework-agnostic and unit
testable without FastAPI. `lib/` introduces no import from `src.api`, preserving the inward
dependency arrow.

## Complexity Tracking

> No Constitution Check violations — table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| (none)    | —          | —                                    |

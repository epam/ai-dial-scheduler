# Phase 0 Research: User Identity Authentication

All Technical Context items are known from the existing codebase and the feature spec. The
items below record the design decisions and the alternatives considered. No open
`NEEDS CLARIFICATION` remain.

## Decision 1: Where the identity is resolved (DIAL `GET /v1/info`)

- **Decision**: Resolve identity by calling `GET {settings.dial_url}/v1/info` with the caller's
  `Api-Key` header (header name from existing `src.lib.constants.API_KEY_HEADER = "Api-Key"`).
- **Rationale**: The feature spec and task instruction state the identity source is DIAL's
  `/v1/info` endpoint, keyed by the per-request `Api-Key`. The header constant and the
  `dial_url` setting already exist; the worker executors already use `API_KEY_HEADER` against
  DIAL, so the convention is established.
- **Alternatives considered**:
  - *Decode the API key / JWT locally* — rejected: the key format is opaque to this service and
    DIAL is the authority on identity; local decoding would duplicate DIAL logic and break on
    key-format changes.
  - *A generic IdP/OIDC userinfo endpoint* — rejected: identity comes from DIAL, not a separate
    IdP (per user correction during specify).

## Decision 2: HTTP client — reuse `app.state.app_client`

- **Decision**: The DIAL call uses the shared `httpx.AsyncClient` already created for the app
  lifespan and stored in `app.state.app_client`. A `get_dial_client` FastAPI dependency reads
  it from the request and wraps it in a thin `DialClient`.
- **Rationale**: Constitution forbids module-level globals and mandates reusable components in
  app state (`Technical Constraints > Web framework`). Reusing the keep-alive pool avoids
  per-request connection setup and inherits the configured limits/timeouts.
- **Alternatives considered**:
  - *New `httpx.AsyncClient()` per request* (as the worker executors do) — rejected for the API
    hot path: it discards connection pooling and adds latency on every authenticated request.
    The executors run in the worker on a per-job basis, a different trade-off.

## Decision 3: Path-extraction semantics

- **Decision**: `user_id_path_list: list[str]` (default `["userClaims.mail.0", "project"]`).
  Each path is split on `.`; a segment that is all digits is treated as a list index, otherwise
  a mapping key. Traverse left-to-right; on any miss (key absent, index out of range, type
  mismatch) skip to the next path. A candidate is accepted only if the final value is a
  non-empty string after `str()`-coercion and `strip()`. First accepted value wins.
- **Rationale**: Matches the task's stated rule set exactly ("dot separated strings as dict keys
  and integer as array indexes", first found wins, non-empty). Keeping it a pure function makes
  it fully unit-testable without HTTP or FastAPI.
- **Alternatives considered**:
  - *JSONPath / `jsonpath-ng` dependency* — rejected (YAGNI): the required grammar is a simple
    dotted path with integer indices; a new dependency is unjustified for two default paths.
  - *Accept any truthy type (int/bool)* — rejected: identity is a user string; coerce and
    require non-empty to avoid `0`/`False`/`""` being treated as a valid id. Whitespace-only is
    treated as empty.

## Decision 4: Failure-to-HTTP mapping

- **Decision**:
  - Missing/empty `Api-Key` header → **401 Unauthorized**.
  - DIAL responds `401`/`403` (key rejected) → **401 Unauthorized**.
  - DIAL reachable & 2xx but no candidate path yields a non-empty value → **401 Unauthorized**.
  - DIAL transport error (timeout/connection) or non-2xx other than 401/403 → **502 Bad Gateway**.
- **Rationale**: Spec FR-006 and User Story 2 require rejecting unidentifiable callers as
  unauthorized; the edge cases require rejecting (never silently admitting) on DIAL
  unreachable/malformed responses. Distinguishing "you are not authorized" (401) from "the auth
  upstream failed" (502) aids diagnosis and matches the existing 502 convention used for
  upstream `JobRegistrationError`. The dependency raises `fastapi.HTTPException`; the lib layer
  raises domain errors (`DialServiceError`, `UserIdNotResolvedError`) that the dependency maps.
- **Alternatives considered**:
  - *Return 401 for everything including DIAL outages* — rejected: masks an upstream outage as a
    client credential problem and misleads callers/operators.
  - *503 for DIAL outage* — acceptable but 502 (bad gateway) better expresses "upstream
    dependency failed" and aligns with the existing 502 usage in these endpoints.

## Decision 5: No identity caching (YAGNI)

- **Decision**: Resolve identity on every request; no in-memory or TTL cache.
- **Rationale**: Spec does not require caching; correctness (immediate revocation, fresh
  identity) is simpler without it. Constitution V favors the simplest sufficient solution.
- **Alternatives considered**: *Per-key TTL cache* — deferred until a measured latency problem
  exists; would add invalidation complexity now with no demonstrated need.

## Decision 6: Test strategy (Test-First)

- **Decision**: Unit-test the pure extractor (`tests/unit/lib/utils/test_user_id.py`) and the
  `DialClient` (mock httpx) first; unit-test `get_user_id` with `app.dependency_overrides` /
  mocked client covering success, missing key, DIAL 401, DIAL outage, and empty-identity cases;
  update existing endpoint unit tests to override `get_user_id`; add an integration test proving
  endpoints scope to the resolved id. Mock httpx via `pytest-mock`/`unittest.mock` (no `respx`
  dependency — not in the lockfile).
- **Rationale**: Constitution III mandates Red-Green-Refactor and full coverage of new code;
  `respx` is absent from dependencies, so mock the client boundary with the existing tooling.

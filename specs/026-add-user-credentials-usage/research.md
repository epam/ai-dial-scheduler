# Phase 0 Research: Per-User Credentials for Schedule Execution

## §1. Where the worker gets `service_id` and `owner_sub` at run time

**Decision**: Read `service_id` and `created_by` from the `schedules` table (new
`ScheduleRepository.get_by_id(schedule_id)`), not from APScheduler job kwargs.

**Rationale**: The migration backfills the `schedules.service_id` column for existing rows, but
APScheduler jobs already persisted in `apscheduler_jobs` keep their original serialized `kwargs`
(built as `{**properties, "schedule_id": ...}`), which contain no `service_id`/`created_by`. On
worker restart those jobs are re-loaded from the jobstore with the old kwargs. Reading from the
`schedules` table is authoritative for both new and pre-existing jobs and requires no rewrite of
the engine-owned jobstore. The worker already opens a DB session per run (to create the
in-progress run), so this adds one indexed PK lookup.

**Alternatives considered**:
- *Inject `service_id`/`created_by` into job kwargs at registration* — fails for pre-existing jobs
  until each is updated; would need a backfill that rewrites APScheduler internal state (fragile).
- *Store `service_id` inside `ScheduleProperties`/`kwargs`* — conflates credential source with
  target-call properties and inherits the same stale-kwargs problem.

## §2. `owner_sub` value and the `user_id_path_list` change

**Decision**: `owner_sub` for the OBO request is the schedule's stored `created_by`.
`user_id_path_list` default becomes `["sub", "project"]`, so newly created schedules store the
subject claim as `created_by`.

**Rationale**: Scheduled runs are unattended — there is no incoming `Api-Key`/token in the worker
to re-resolve identity, so the owner must come from what was stored at create time. DIAL's OBO
`owner_sub` expects the user subject; `sub` is the correct claim. `extract_user_id` already walks
the ordered path list against the DIAL `/v1/user/info` body, so only the default list changes.

**Consequence (accepted)**: Schedules created before this change stored an email-style
`created_by` (from `userClaims.email.0`). Their OBO lookup will pass that email as `owner_sub`;
if DIAL cannot resolve it, the run is recorded as a credentials error (spec User Story 2,
Scenario 3 — best-effort, no bulk backfill of `created_by`).

**Alternatives considered**: Backfilling `created_by` to subjects — rejected by the user
(best-effort chosen); no reliable email→sub mapping available offline.

## §3. Applying the OBO header across executors

**Decision**: Carry `auth_header_name: str` and `auth_header_value: SecretStr` on `RunContext`.
Each executor builds outgoing headers from `extra_headers` and then sets the OBO header (OBO wins
on name collisions); the shared `settings.dial_api_key` injection is removed entirely.

- **REST/SSE**: `headers = {**properties.extra_headers, header_name: header_value}`. No `Api-Key`
  from settings is added. If `extra_headers` happened to contain the same header name, the OBO
  value overrides it.
- **chat_completion (AsyncAzureOpenAI)**: set the client's `api_key` constructor argument to the
  OBO `header_value` — and if that value starts with the `"Bearer "` prefix (OAUTH-type services),
  strip the prefix so only the raw token is passed as `api_key`. Do NOT pass the shared
  `settings.dial_api_key` and do NOT add a separate auth `extra_headers` entry for chat_completion
  (the SDK emits the auth header from `api_key`). `properties.extra_headers` is still forwarded for
  any caller-supplied non-auth headers. This keeps both auth types on one code path: API_KEY values
  pass through unchanged; OAUTH `"Bearer <token>"` values have the prefix removed.

**Rationale**: Keeps a single, generic code path (`header_name`/`header_value`) for REST/SSE and a
minimal, correct adaptation for the OpenAI SDK. `SecretStr` prevents accidental logging. The
generic header handles both the `Api-Key` header (API_KEY services) and the `Authorization: Bearer`
header (OAUTH services) — see §8.

**Alternatives considered**: A shared helper that mutates a headers dict — deferred (YAGNI; three
call sites, each one line). `default_headers` on the AsyncAzureOpenAI constructor — equivalent;
per-request `extra_headers` is already wired and scoped to the call.

## §4. OBO credentials call contract

**Decision**: Add `DialClient.get_obo_credentials(request: OboCredentialsRequest) -> OboCredentials`.

- Method/URL: `POST {dial_url}/v1/ops/external-service/obo-credentials`
- Auth: `Api-Key: {settings.dial_api_key}` (the scheduler service key authenticates the OBO
  request itself — this is NOT sent to the target).
- Body: `{"url": "applications/{scheduler_app_id}/external_services/{service_id}", "owner_sub": "{owner_sub}"}`
- Success (2xx): parse `header_name` and `header_value` from the response body.
- Failure ("credential not found" / non-2xx / transport error): surfaced to the caller with the
  response status and body so the worker can persist it in the run result.

**Rationale**: Mirrors the existing `get_user_info` pattern in the same client. Using an explicit
Pydantic request/response model satisfies Constitution IV (typed cross-service contracts).

**Confirmed against a live `dial-core` (v0.46.0-dev.35)** during implementation, via
`tests/integration/test_real_dial_credentials.py`: for the `dial-oauth` (OAUTH) service, a
successful OBO call returns
`{"header_name": "Authorization", "header_value": "Bearer <token>", "expires_at": <epoch>}`.
`OboCredentials` correctly models `header_name`/`header_value`; the extra `expires_at` field is
silently ignored by Pydantic's default `extra="ignore"`, which is fine since credentials are
fetched fresh per run (no caching, per FR-011) — `expires_at` is not needed. See
[contracts/obo-credentials.md](./contracts/obo-credentials.md).

## §5. Recording a distinguishable credentials error

**Decision**: Extend the error result shape so a credentials failure is distinguishable from a
target-request failure (FR-010). Introduce a small `RunError` Pydantic model and a repository
method that stores a structured result, e.g.:

```json
{ "stage": "credentials", "error_text": "...", "credentials_response": { "status": 404, "body": {...} } }
```

Target-request failures keep `stage = "target"` (or the existing `{"error_text": ...}` shape,
extended with `stage`). The secret `header_value` is never part of any stored/failed response
(the failure response from DIAL does not contain issued credentials).

**Rationale**: Operators can filter failed runs by `stage`; SC-005 is verifiable. Reuses the
existing `result` JSONB column — no new DB column needed.

**Alternatives considered**: A dedicated `error_stage` column on `schedule_runs` — heavier
(migration + model change) for information that fits naturally in the existing `result` JSONB.

## §6. Settings additions

**Decision**:
- Add `scheduler_app_id: str = "dial_scheduler"` to `Settings`.
- Change `user_id_path_list` default to `["sub", "project"]`.

**Rationale**: Directly from the feature description. `scheduler_app_id` composes the OBO `url`
target. Both are plain settings fields with defaults, overridable via environment.

## §7. MCP guidance for `service_id`

**Decision**: MCP tools are generated from the FastAPI OpenAPI contract; there is no hand-written
tool file. Add guidance in the `create_schedule`/`update_schedule` endpoint docstrings and add
`service_id` to `_SCHEDULE_EXAMPLES` in `src/api/v1/endpoints/schedules.py`, explicitly stating:
"Use `service_id: \"dial-oauth\"` unless a different external service is required." This text flows
into the OpenAPI description that FastMCP surfaces to MCP clients.

**Rationale**: Single source of truth (OpenAPI). The API requires `service_id` (per clarification),
and the guidance tells MCP clients what value to use by default.

## §8. External-service auth types: API_KEY and OAUTH (both must be covered)

**Context**: `dial/core/config-template.json` registers the scheduler app (`dial_scheduler`) with
`allow_user_external_services: true` and TWO external services:

- `dial-api-key` — `authentication_type: API_KEY`, `api_key_header: "Api-Key"`. OBO returns the
  per-user value to send in the `Api-Key` header.
- `dial-oauth` — `authentication_type: OAUTH` (Keycloak realm `dial`). OBO returns a bearer token
  to send in the `Authorization: Bearer <token>` header.

**Decision**: The generic `header_name`/`header_value` design covers both without branching in the
executors. Tests MUST cover BOTH shapes:

- API_KEY path → `header_name = "Api-Key"`, plain value.
- OAUTH path → `header_name = "Authorization"`, value `"Bearer <token>"`.

The default backfill/service for legacy schedules is `dial-oauth` (OAUTH), so the OAUTH path is the
primary case; the API_KEY path (`dial-api-key`) must be exercised explicitly to prevent regressions
when a schedule selects an API_KEY-type service.

**Rationale**: `config-template.json` is the source of truth for which services and auth types the
scheduler app exposes. Locking both into unit tests guards the generic header contract.

## §9. Real-DIAL integration & the sign-in helper

**Decision**: Use `scripts/dial_sign_in.py` to establish real OBO credentials in integration tests
that run against a live local DIAL stack (`dial/docker-compose.yml`). The script issues a Keycloak
user token (password grant), drives the login form to get an authorization code, and calls DIAL's
`POST /v1/ops/external-service/signin` for `applications/dial_scheduler/external_services/dial-oauth`
— after which the scheduler's OBO fetch (`.../obo-credentials`) returns valid credentials for that
user. The script prints the token's `sub`, which is the `owner_sub`/`created_by` a schedule must
carry for the OBO lookup to resolve.

**Rationale**: OAUTH OBO cannot be exercised end-to-end without a signed-in user; the helper makes
this reproducible and keeps real-DIAL integration tests deterministic. Mock-based unit tests still
cover both auth types offline (§8); the real-DIAL integration test is opt-in (requires the local
stack) and documents the sign-in prerequisite.

**Deliverables**: document the helper (and the sign-in prerequisite) in the README testing section
and reference it from the real-DIAL integration test; note the `sign-in-dependency.md` the script
docstring refers to should be authored (or its content folded into the README) during
implementation.

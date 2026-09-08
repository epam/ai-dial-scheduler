---

description: "Task list for Scheduler MCP Sign-In Status Signal"
---

# Tasks: Scheduler MCP Sign-In Status Signal

**Input**: Design documents from `specs/037-mcp-signin-notifications/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/sign-in-status.md, quickstart.md

**Tests**: Included — the project constitution (Principle III, NON-NEGOTIABLE) mandates TDD;
every implementation task below has a corresponding test task that MUST be written and
confirmed failing first.

**Organization**: Tasks are grouped by user story (spec.md P1/P2/P3) so each can be implemented,
tested, and demoed independently. There is no Setup phase: this feature adds no new dependency,
config, or project scaffolding — every new file slots into an existing package
(`src/api/dependencies/`, `src/api/v1/endpoints/`, `src/lib/schemas/`, `src/lib/services/`,
`src/lib/exceptions/`), and every target test directory already exists with an `__init__.py`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Paths are single-project (`src/`, `tests/`) per plan.md's Structure Decision

---

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: The `SignInStatus` contract and the `SignInService.check()` probe that both User
Story 1 (status endpoint) and User Story 2 (create/update precondition) build on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T001 [P] Unit test for `SignInStatus` in `tests/unit/lib/schemas/test_sign_in.py`: model
  accepts `service_id`/`state`/`detail` and derives `signed_in` from `state == "signed_in"`;
  rejects a `state` value outside `signed_in`/`not_signed_in`/`unknown`. Confirm it fails
  (module does not exist yet).
- [X] T002 [P] Unit test for `SignInService.check()` in
  `tests/unit/lib/services/test_sign_in_service.py`: given a stubbed `DialClient`, both
  `user_level_auth_status`/`app_level_auth_status` `SIGNED_IN` maps to `state="signed_in"`;
  `user_level_auth_status="SIGNED_OUT"` with `app_level_auth_status="SIGNED_IN"` maps to
  `state="not_signed_in"`; `app_level_auth_status != "SIGNED_IN"` (checked first) maps to
  `state="unknown"`; a `DialServiceError` from the client maps to `state="unknown"`. Confirm it
  fails (module does not exist yet). *(Superseded during code review, see T035 below: the
  original version of this test targeted an OBO-credentials-probe mapping instead.)*
- [X] T003 [P] Unit test for `get_sign_in_service` DI provider in
  `tests/unit/api/dependencies/test_sign_in.py`: given a request with `app.state.app_client`
  and the app's `Settings`, returns a `SignInService` wired to a `DialClient` built the same
  way `get_dial_client` builds one (reuse pattern from `src/api/dependencies/auth.py`).
  Confirm it fails (module does not exist yet).
- [X] T004 [P] Create `SignInStatus` Pydantic model in `src/lib/schemas/sign_in.py` per
  data-model.md: `service_id: str`, `state: Literal["signed_in", "not_signed_in", "unknown"]`,
  `detail: str`, `signed_in: bool` computed from `state`. Makes T001 pass.
- [X] T005 Implement `SignInService.check(user_id: str, service_id: str, caller_api_key: str,
  channel_id: str | None = None) -> SignInStatus` in `src/lib/services/sign_in_service.py`,
  calling `DialClient.get_external_service_auth_status(caller_api_key, scheduler_app_id,
  service_id)` (authenticated as the caller, not the scheduler's own `dial_api_key`) and
  mapping the outcome per research.md §1–2. Log `sign_in_check_started`/`sign_in_check_result`
  via `src.lib.utils.logging.logger` (never log a secret). Depends on T004; makes T002 pass.
  *(Superseded during code review, see T035: originally called
  `DialClient.get_obo_credentials(dial_api_key, OboCredentialsRequest(...))` instead.)*
- [X] T006 Add `get_sign_in_service(dial_client: Annotated[DialClient, Depends(get_dial_client)],
  settings: Annotated[Settings, Depends(get_settings_dep)]) -> SignInService` provider in
  `src/api/dependencies/sign_in.py`, reusing `get_dial_client`/`get_settings_dep` from
  `src/api/dependencies/auth.py` instead of duplicating `request.app.state` access. Depends on
  T005; makes T003 pass.

**Checkpoint**: `SignInService.check()` is fully functional and unit-tested. User Story 1 and
User Story 2 can now proceed (in parallel, if staffed).

---

## Phase 2: User Story 1 - Assistant learns sign-in state before creating a schedule (Priority: P1) 🎯 MVP

**Goal**: An assistant can call a read-only MCP tool / HTTP endpoint and get back a structured
signed_in / not_signed_in / unknown answer for a given service, with no side effects.

**Independent Test**: Call `GET /v1/sign-in/status?service_id=...` for a user known not to be
signed in; assert a structured `not_signed_in` result. Repeat for a signed-in user and for a
simulated DIAL outage. No dependency on schedule create/update.

### Tests for User Story 1 ⚠️

> Write these first; confirm they fail before implementing.

- [X] T007 [P] [US1] Unit test for `GET /v1/sign-in/status` in
  `tests/unit/api/v1/endpoints/test_sign_in.py`: 200 + correct body for each of
  signed_in/not_signed_in/unknown (via a mocked `SignInService`); 400 when `service_id` is
  missing; asserts no repository/scheduler dependency is touched.
- [X] T008 [P] [US1] Integration test in `tests/integration/test_sign_in_api.py`: end-to-end
  request through the real FastAPI app with `get_sign_in_service` overridden, covering the
  three `state` values and the response shape from contracts/sign-in-status.md.
- [X] T009 [P] [US1] Update `tests/unit/api/mcp/test_mcp_tools.py` and
  `tests/unit/api/mcp/test_server.py` (already list the endpoint's presence — extend the
  expected-tool-names assertion) to assert `get_sign_in_status` is present in the MCP tool
  list, and that its input schema has no `Api-Key` parameter (per the existing header-stripping
  behavior in `src/api/mcp/server.py`).

### Implementation for User Story 1

- [X] T010 [US1] Add `GET /v1/sign-in/status` endpoint in `src/api/v1/endpoints/sign_in.py`,
  tagged `sign-in`, taking `service_id` as a required query parameter and the caller's
  `user_id` via the existing `get_user_id` dependency, calling
  `sign_in_service.check(user_id, service_id)` and returning it as the response model.
  Depends on T006; makes T007/T008 pass.
- [X] T011 [US1] Register `sign_in_router` in `src/api/app.py` (mirrors how the `schedules`/
  `runs` routers are already registered).
- [X] T012 [US1] Add `RouteMap(tags={"sign-in"}, mcp_type=MCPType.TOOL)` to `ROUTE_MAPS` in
  `src/api/mcp/route_maps.py`, and update the docstring in `src/api/mcp/server.py` listing
  which tags become tools. Makes T009 pass.

**Checkpoint**: User Story 1 is fully functional and independently testable/demoable — an
assistant can check sign-in status via MCP today, with no popup yet (by design, per FR-011).

---

## Phase 3: User Story 2 - Creating or updating a schedule is blocked until sign-in (Priority: P2)

**Goal**: Schedule create/update requests fail closed (403 not_signed_in / 502 unknown, no
schedule created/modified) when the target service isn't signed in.

**Independent Test**: Attempt to create, and separately update, a schedule for a service the
test user is not signed in to; assert rejection with the structured payload and that no
schedule row is created/changed. Repeat for an outage, expecting 502 instead of 403.

### Tests for User Story 2 ⚠️

> Write these first; confirm they fail before implementing.

- [X] T013 [P] [US2] Unit test for `SignInService.require()` in
  `tests/unit/lib/services/test_sign_in_service.py`: returns normally when `check()` yields
  `signed_in`; raises `SignInRequiredError` carrying `service_id`/`state`/`detail` for
  `not_signed_in` and for `unknown`. Confirm it fails (method does not exist yet).
- [X] T014 [P] [US2] Unit tests for the create/update precondition in
  `tests/unit/api/v1/endpoints/test_schedules.py`: `POST`/`PUT` return 403 with the documented
  detail shape when `SignInRequiredError(state="not_signed_in")` is raised; 502 when
  `state="unknown"`; the underlying repository call is never made in either case; a malformed
  request body is still rejected before the sign-in check runs (no call to `require()`).
- [X] T015 [P] [US2] Add a `get_sign_in_service` dependency override fixture used by
  `tests/integration/test_runs_api.py`, `tests/integration/test_schedule_metadata.py`,
  `tests/integration/test_schedules_api.py`, and `tests/integration/test_user_id_auth.py` (a
  "signed in" default so these pre-existing suites are unaffected by the new precondition),
  plus new integration assertions in `test_schedules_api.py` for the blocked create/update path.

### Implementation for User Story 2

- [X] T016 [US2] Add `SignInRequiredError(SchedulerError)` in `src/lib/exceptions/errors.py`.
  Implemented wrapping a `SignInStatus` (`__init__(self, status: SignInStatus)`) rather than
  three loose parameters, per the constitution's 2–3 parameter rule; exposes `service_id`/
  `state`/`detail` as properties delegating to it. Makes the raise side of T013 possible.
- [X] T017 [US2] Implement `SignInService.require(user_id: str, service_id: str) -> None` in
  `src/lib/services/sign_in_service.py` (calls `check()`, raises `SignInRequiredError` when
  `state != "signed_in"`). Depends on T005, T016; makes T013 pass.
- [X] T018 [US2] Add a `_require_sign_in` helper and call it from both the create and update
  handlers in `src/api/v1/endpoints/schedules.py`, after body validation and `get_user_id`
  resolution and before any repository write; catch `SignInRequiredError` and map
  `not_signed_in` → 403 / `unknown` → 502 with the structured detail body from
  contracts/sign-in-status.md. Depends on T017; makes T014 pass.
- [X] T019 [US2] Wire `SignInServiceDep = Annotated[SignInService, Depends(get_sign_in_service)]`
  into the create/update route signatures in `src/api/v1/endpoints/schedules.py`. Makes T015's
  overrides take effect; makes T015 pass.

**Checkpoint**: User Stories 1 AND 2 both work independently — schedules can no longer be
created/updated against a service the caller isn't signed in to, and the assistant can check
status proactively via US1 before even attempting it.

---

## Phase 4: User Story 3 - The sign-in signal is shaped for a future UI popup (Priority: P3)

**Goal**: Lock in, with a test, that the payload shape already produced by Phases 1–3 carries
everything a future popup integration would need — so this is a verification story, not new
runtime behavior.

**Independent Test**: Given only the documented `SignInStatus`/`SignInRequiredError` payload
shape, confirm a consumer can identify the affected service and the signed_in/not_signed_in
distinction using machine-readable fields alone, with no free-text parsing.

### Tests for User Story 3 ⚠️

- [X] T020 [P] [US3] Contract-shape test in `tests/unit/lib/schemas/test_sign_in.py` (extends
  T001's file): asserts `SignInStatus.model_json_schema()` marks `service_id` and `state` as
  required fields with `state` an enum of exactly `["signed_in", "not_signed_in", "unknown"]`,
  and that `detail` carries no such constraint (documenting it as free text, per FR-010).
- [X] T021 [P] [US3] Contract-shape test for `SignInRequiredError` in
  `tests/unit/lib/exceptions/test_errors.py`. That file already existed (for `ScheduleRunError`)
  rather than needing to be created fresh; extended it with a `TestSignInRequiredError` class
  asserting the exception exposes `service_id`/`state`/`detail` as plain attributes usable
  without parsing `str(exc)`.

### Implementation for User Story 3

- [X] T022 [US3] No production code change expected — if T020/T021 fail, adjust
  `src/lib/schemas/sign_in.py` / `src/lib/exceptions/errors.py` field declarations (from Phase
  1/3) until they pass, then double check contracts/sign-in-status.md's forward-compatibility
  note still matches the shipped shape exactly.

**Checkpoint**: All three user stories are independently functional. The response contract is
verified stable enough for a popup integration to consume without another change here — Phase 6
below implements exactly that, sooner than expected at this point.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Final quality gates across all stories.

- [X] T023 [P] Update `documentation/api_contract.md` with the `GET /v1/sign-in/status`
  endpoint and the sign-in precondition on create/update (mirrors contracts/sign-in-status.md).
- [X] T024 [P] Update `README.md`'s sign-in section (if present) / add one documenting
  `get_sign_in_status` and the create/update precondition for a human reader, per the level of
  detail the previous attempt had (now restored from scratch against the new contract).
- [X] T025 Run `uv run ruff check src tests` and `uv run ruff format --check src tests`; fix
  any violations.
- [X] T026 Run `uv run mypy src`; fix any violations, particularly the `Literal` typing on
  `SignInStatus.state`/`SignInRequiredError.state`.
- [X] T027 Run `uv run pytest --cov=src`; confirm all new and existing tests pass, including
  the four integration suites touched by T015.
- [ ] T028 Execute quickstart.md's six scenarios end-to-end against a running instance;
  confirm each matches its documented expectation.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 1)**: No dependencies — BLOCKS User Story 1 and User Story 2.
- **User Story 1 (Phase 2)**: Depends on Phase 1. Independent of User Story 2.
- **User Story 2 (Phase 3)**: Depends on Phase 1. Independent of User Story 1 (does not require
  the `GET /v1/sign-in/status` route to exist — it calls `SignInService.require()` directly).
- **User Story 3 (Phase 4)**: Depends on Phase 1 (schema) and Phase 3 (`SignInRequiredError`)
  existing, since it verifies both shapes; it adds no new runtime code of its own.
- **Polish (Phase 5)**: Depends on all desired user stories being complete.

### Parallel Opportunities

- T001–T003 (foundational tests) run in parallel; T004 can start once T001 exists.
- Once Phase 1's checkpoint is reached, Phase 2 and Phase 3 can proceed in parallel (different
  files: `sign_in.py` endpoint/route-map vs. `schedules.py`/`errors.py`), though both read
  `sign_in_service.py`, so land T005/T006 before branching.
- T007–T009 in parallel; T013–T015 in parallel; T020–T021 in parallel.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Foundational).
2. Complete Phase 2 (User Story 1) — `get_sign_in_status` is usable by an assistant.
3. **STOP and VALIDATE**: run quickstart.md Scenarios 1–3 and 6.
4. This alone is deployable value: an assistant can already tell a user "you need to sign in to
   X" today, from the MCP tool response — the precondition (US2) only tightens enforcement.

### Incremental Delivery

1. Phase 1 → Phase 2 (MVP: status visibility) → Phase 3 (enforcement) → Phase 4 (contract
   lock-in) → Phase 5 (polish/docs/quality gates).

---

## Phase 6: User Story 4 - Interactive popup via client-channel (added post-implementation)

Confirmed feasible and implemented after discovering (via `ai-dial-quickapps-backend`'s
`interactive_login.md` design doc) that agent frameworks already forward `X-*` headers,
including `X-DIAL-CLIENT-CHANNEL-ID`, to MCP servers without any DIAL Core change — closing the
gap FR-011 originally deferred as a cross-repo dependency.

- [X] T029 Add `CLIENT_CHANNEL_HEADER`, `CLIENT_CHANNEL_INTERACT_PATH`,
  `EXTERNAL_SERVICE_SIGNIN_METHOD` to `src/lib/constants.py`; add
  `client_channel_signin_timeout_seconds` to `Settings`.
- [X] T030 Implement `DialClient.request_external_service_signin(api_key, channel_id, scope,
  timeout)` in `src/lib/clients/dial_client.py`: POST a single-entry JSON-RPC batch to
  `/v1/ops/client-channel/interact`, read the SSE response via `httpx-sse`, normalize both the
  list-of-responses and single-error-object shapes Core can return, wrap in `asyncio.timeout`,
  never raise. Add `tests/unit/lib/clients/test_dial_client.py::TestRequestExternalServiceSignin`.
- [X] T031 Thread `channel_id`/`caller_api_key` through `SignInService.check()`/`.require()`
  (`src/lib/services/sign_in_service.py`): on a non-`signed_in` probe with both present, call
  T030's method **as the caller**, not the scheduler's own `dial_api_key` — required because
  DIAL Core resolves a client channel by the caller's bucket (research.md §5). Re-probe once on
  success. Add `TestSignInServiceChannelInteract` covering trigger/success/denied/no-channel/
  no-api-key/already-signed-in.
- [X] T032 Forward `X-DIAL-CLIENT-CHANNEL-ID` the same way `Api-Key` is already forwarded:
  `forward_client_channel_id` hook in `src/api/mcp/headers.py`, registered alongside
  `forward_api_key` in `src/api/mcp/server.py`'s event hooks and header-stripping set. Add
  `TestForwardClientChannelId` to `tests/unit/api/mcp/test_headers.py`.
- [X] T033 Accept the caller's own `Api-Key` and the optional channel header directly on
  `GET /v1/sign-in/status`, `POST /v1/schedules/`, and `PUT /v1/schedules/{id}` (not just via
  the `get_user_id` dependency), and pass both through to `SignInService`. Update existing
  endpoint tests' `TestClient`/mock-app fixtures to send a default `Api-Key` header.
- [X] T034 Verify end-to-end against a live local stack (`docker compose build/up`): subscribe
  to a real client channel, trigger a not-signed-in check through DIAL Core's deployment-route
  proxy, confirm the `external-service/signin` event is delivered on the subscribed stream, and
  confirm `/report` resolves the scheduler's still-open interact call without error. Reproduced
  and fixed two real bugs in the process (see research.md §5): the caller-identity requirement,
  and the single-error-object response shape.

---

## Phase 7: Replace the OBO probe with the application-status endpoint (post-implementation, code review)

Code review pointed out that `SignInService._probe()` did not need to mint a throwaway OBO
credential grant just to observe whether DIAL would issue one — `GET
/v1/applications/{scheduler_app_id}` already carries the exact status DIAL Core computes,
`user_level_auth_status`/`app_level_auth_status`, for whichever identity authenticates the
request. Since every call site already has the caller's own forwarded `Api-Key` (added in
Phase 6 for the client-channel popup), reading that status as the caller is strictly cheaper and
more precise than the OBO probe (see research.md §1, "Superseded approach").

- [X] T035 Add `ExternalServiceAuthStatus` (`user_level_auth_status`,
  `app_level_auth_status: Literal["SIGNED_IN", "SIGNED_OUT", "FAILED"]`) to
  `src/lib/schemas/sign_in.py`; add `APPLICATION_STATUS_PATH_TEMPLATE` to
  `src/lib/constants.py`. Implement `DialClient.get_external_service_auth_status(api_key,
  app_id, service_id) -> ExternalServiceAuthStatus` in `src/lib/clients/dial_client.py`: `GET
  /v1/applications/{app_id}`, parse `external_services.{service_id}.auth_settings`, raise
  `DialServiceError` on non-2xx/transport error, a missing `service_id` entry, or a malformed
  `auth_settings` body. Add `TestGetExternalServiceAuthStatus` to
  `tests/unit/lib/clients/test_dial_client.py`.
- [X] T036 Rewrite `SignInService._probe()` to call T035's method with the caller's own
  `caller_api_key` instead of `DialClient.get_obo_credentials`; drop `dial_api_key` from
  `SignInService.__init__` entirely (no longer needed anywhere in this class). Make
  `caller_api_key` a required, non-optional parameter of `check()`/`require()` (positioned
  before the still-optional `channel_id`). Update `get_sign_in_service`
  (`src/api/dependencies/sign_in.py`) and both call sites
  (`src/api/v1/endpoints/sign_in.py`, `src/api/v1/endpoints/schedules.py`'s
  `_require_sign_in`) for the new parameter order. Rewrite
  `tests/unit/lib/services/test_sign_in_service.py`,
  `tests/unit/api/dependencies/test_sign_in.py`, and the affected assertions in
  `tests/unit/api/v1/endpoints/test_sign_in.py`/`test_schedules.py` accordingly.
- [X] T037 Verify both branches end-to-end against a live local stack (rebuilt `api`/`worker`
  images, real Core + Keycloak, no mocks): a project-scoped Api-Key with no personal offline
  credentials connected returns `not_signed_in` through the real
  `GET /v1/sign-in/status` endpoint; a real Keycloak user with offline credentials connected
  returns `signed_in` through the real MCP proxy path
  (`POST /v1/deployments/applications/{app_id}/mcp` → `get_sign_in_status` tool call) —
  confirming the caller-identity plumbing (per-request key minted by Core, forwarded as
  `Api-Key` to the scheduler) works for this rewritten probe exactly as it already did for the
  Phase 6 popup path.

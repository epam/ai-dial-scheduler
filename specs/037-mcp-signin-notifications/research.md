# Research: Scheduler MCP Sign-In Status Signal

No `NEEDS CLARIFICATION` markers remained in the Technical Context after drafting — every open
question below was resolved by reading the existing codebase (this feature builds entirely on
mechanisms DIAL and dial-scheduler already have) rather than by external research. Documented
here per the Phase 0 format for traceability.

## 1. How is "signed in" determined?

**Decision**: Call `DialClient.get_external_service_auth_status(caller_api_key, scheduler_app_id,
service_id)`, which issues `GET {dial_url}/v1/applications/{scheduler_app_id}` authenticated
with **the caller's own forwarded `Api-Key`** (not the scheduler's own `dial_api_key`), and
parses `external_services.{service_id}.auth_settings.{user_level_auth_status,
app_level_auth_status}` into `ExternalServiceAuthStatus`. `app_level_auth_status != "SIGNED_IN"`
means an administrator has not approved this service for the application (checked first, since
no amount of user sign-in fixes it); otherwise `user_level_auth_status == "SIGNED_IN"` means the
user is signed in, and anything else means they are not.

**Rationale**: Revised from an earlier implementation that minted a throwaway on-behalf-of (OBO)
credential grant via `DialClient.get_obo_credentials` purely to observe whether DIAL would
issue one (a 403 meant "not signed in"). Code review (see "Superseded approach" below) pointed
out DIAL Core already computes and exposes this exact status on the application-details
response for whichever identity the request authenticates as — at every point this feature
calls it, that identity is the caller themselves (the `Api-Key` forwarded by MCP/the request
header), so reading the status directly is both cheaper (no credential is minted or discarded)
and more precise (it also surfaces the app-level/admin-consent state, which the OBO probe could
not distinguish from "not signed in"). DIAL is still the only system with authoritative
knowledge of a user's external-service connection; this decision only changes which of its
endpoints is queried.

**Alternatives considered**:
- *Add a new DIAL endpoint dedicated to sign-in status* — unnecessary once `GET
  /v1/applications/{id}` was confirmed to already carry this data for the caller's own identity.
- *Cache the result* — rejected per Simplicity/YAGNI and to avoid ever reporting a stale
  "signed in" after the user disconnects; this is a single lightweight HTTP call, same cost
  class as the superseded OBO probe.

**Superseded approach** (kept here for traceability, not followed): the OBO probe
(`DialClient.get_obo_credentials(dial_api_key, OboCredentialsRequest(url=EXTERNAL_SERVICE_URL_TEMPLATE.format(...),
owner_user_id=user_id))`, authenticated as the scheduler's own `dial_api_key`) mapped a 2xx to
`signed_in` and `OboCredentialsError(status_code=403)` to `not_signed_in`. It worked but
required the scheduler to attempt a real credential mint on every check, and had no way to
separate "user not signed in" from "admin has not approved this service" — both surfaced as the
same 403.

## 2. What does "not signed in" vs. "unable to determine" look like at the HTTP layer?

**Decision**: Map `app_level_auth_status != "SIGNED_IN"` → `unknown` (an administrator has not
approved this service for the application; signing in cannot fix this). Otherwise map
`user_level_auth_status == "SIGNED_IN"` → `signed_in`, anything else → `not_signed_in`. Any
`DialServiceError` from `get_external_service_auth_status` (non-2xx, transport failure, the
service missing from `external_services`, or a malformed `auth_settings` body) → `unknown`. The
read-only status endpoint always returns `200`, with `unknown` reported via the `state` field
like the other two states — it never raises past the API boundary, since it is advisory and
blocks nothing by itself (see contracts/sign-in-status.md's `Response — 200`). The create/update
precondition, which does gate a write, maps `not_signed_in` → `403` and `unknown` → `502`,
matching the existing convention in this codebase for "DIAL unreachable" (see `get_user_id` in
`src/api/dependencies/auth.py`, which already maps `DialServiceError` → 502).

**Rationale**: `user_level_auth_status`/`app_level_auth_status` are DIAL Core's own explicit
enum (`ResourceAuthStatus { SIGNED_IN, SIGNED_OUT, FAILED }`) computed for the calling identity —
there is no ambiguity to interpret, unlike inferring meaning from an OBO error's status code.
Splitting on `app_level_auth_status` first avoids ever telling a user to sign in when the real
blocker is a missing admin consent (this is FR-004/FR-008; a real Postman response captured
against the local stack was used to confirm the exact response shape:
`external_services.dial-native.auth_settings.{user_level_auth_status: "SIGNED_OUT",
app_level_auth_status: "SIGNED_OUT"}` for a fresh, never-connected identity).

**Alternatives considered**:
- *Treat every non-`SIGNED_IN` outcome as `not_signed_in`* — rejected: would misreport a missing
  admin consent as something the user can fix by signing in, violating FR-004 and SC-004.

## 3. Where does the precondition run relative to existing validation and persistence?

**Decision**: After request-body validation (Pydantic) and after user-identity resolution
(`get_user_id`), but strictly before any repository write. Implemented as a small async helper
(`_require_sign_in`) called from both the create and update handlers in
`src/api/v1/endpoints/schedules.py`, using the request body's `service_id` field (already
present on `ScheduleRequest` since feature 026) and the caller's own forwarded `Api-Key`.

**Rationale**: Matches FR-005/FR-006 ("no schedule is created/modified") and the previous
attempt's own test, `test_invalid_body_is_rejected_before_the_sign_in_check`, which established
that malformed input should fail on its own terms first, without spending a DIAL round-trip on
a request that would be rejected anyway.

**Alternatives considered**:
- *Check sign-in inside `ScheduleService`* — rejected: `ScheduleService` is DB-only today ( see
  `src/api/dependencies/scheduler.py`'s DI wiring, which only injects `ScheduleRepository`,
  `ScheduleRunRepository`, and the APScheduler instance); introducing a DIAL dependency into it
  would blur the API/worker layering the constitution draws around `lib/`. An endpoint-level
  precondition keeps the DIAL call at the API boundary alongside the other DIAL-touching
  dependency (`get_dial_client`).

## 4. How is the payload shaped so a future UI popup can consume it without further rework?

**Decision**: `SignInStatus` carries four fields: `service_id: str` (stable identifier, same
value passed in), `signed_in: bool` derived state for simple truthy checks, `state: Literal[
"signed_in", "not_signed_in", "unknown"]` as the authoritative machine-readable field FR-002
requires (kept distinct from `signed_in` so "unknown" is never coerced into a boolean), and
`detail: str` — the prose an assistant may relay verbatim, explicitly documented as unstable/
free-text so a future consumer never parses it. The 403 precondition rejection returns the same
shape nested under the HTTP error `detail`.

**Rationale**: Directly satisfies FR-003/FR-010/SC-003/SC-005 — a future DIAL Core/ai-dial-chat
integration (out of scope here) needs only `service_id` and `state` to build a popup; `detail`
remains free to reword without becoming a breaking change, because nothing machine-readable
depends on its text.

**Alternatives considered**:
- *Single `status: str` free-text field* — rejected: exactly what FR-002/SC-001 forbid ("never
  an ambiguous or purely textual answer").

## 5. How is the popup actually triggered, and as whom?

**Decision**: When the caller forwards `X-DIAL-CLIENT-CHANNEL-ID` and the probe reports
anything but `signed_in`, call `POST {dial_url}/v1/ops/client-channel/interact` with a
single-entry JSON-RPC batch (`method: "external-service/signin"`, `params.url` = the same
`EXTERNAL_SERVICE_URL_TEMPLATE` scope used for the OBO probe), read the SSE response with
`httpx-sse`, and treat `result == "success"` as a signal to re-run the probe once. Critically,
this call authenticates as **the caller's own forwarded `Api-Key`**, not the scheduler's own
`dial_api_key`.

**Rationale**: Verified empirically against a live local DIAL Core
(`ai-dial-core` `ClientChannelService.toRedisKey`): a client channel's Redis key is derived from
`BucketBuilder.buildBucket(context)` — the *caller's own* bucket. A subscribing chat session
and an interacting deployment that authenticate as different identities (e.g. the scheduler's
own project key vs. the user's session) resolve to different buckets, and Core's
`updateClientChannelState` throws `Client channel is not found for the given ID` even though
the channel demonstrably exists — confirmed by reproducing this exact failure with the
scheduler's own key, then fixing it by switching to the caller's forwarded key and observing
the event correctly arrive on the subscribed side. There is no owner_user_id-style field on
this endpoint to address a specific user's bucket from a service identity, unlike
`/obo-credentials` — the caller's own identity *is* the addressing mechanism here.

**Alternatives considered**:
- *Interact as the scheduler's own service identity* (mirroring the OBO probe) — this was the
  first implementation and it silently failed with "channel not found" for every real chat
  session, since the scheduler's bucket never matches the user's. Ruled out by the reproduction
  above, not by design review.
- *Have DIAL Core thread a channel id into MCP tool calls itself* — this was the assumption at
  the start of this feature (see the now-superseded FR-011 wording in earlier revisions of
  spec.md) — turned out to be unnecessary: the calling agent framework (confirmed in
  `ai-dial-quickapps-backend`) already forwards every `X-*` header, including
  `X-DIAL-CLIENT-CHANNEL-ID`, to MCP servers on its own tool calls, so dial-scheduler receives
  it for free without any DIAL Core change.

**Response shape note**: Core's `/interact` response is a JSON array of RPC-response objects on
success, but a single flat error object (e.g. `{"id": null, "error": {...}}`) on a channel-level
failure — not wrapped in a list. `DialClient.request_external_service_signin` normalizes both
shapes before matching on `id`.

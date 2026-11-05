# Contract (provided): Sign-In Status

Read-only endpoint the scheduler **provides**. Auto-published to MCP as the tool
`get_sign_in_status` via the `sign-in` OpenAPI tag (`src/api/mcp/route_maps.py`).

## Request

```
GET {scheduler_base_url}/v1/sign-in/status?service_id={service_id}
Api-Key: {caller's DIAL API key}
X-DIAL-CLIENT-CHANNEL-ID: {optional client-channel id}
```

- `service_id` — required query parameter; the external service to check (e.g. `dial-native`).
- The caller's identity is resolved the same way every other endpoint resolves it
  (`get_user_id` in `src/api/dependencies/auth.py`) — no new auth mechanism.
- `X-DIAL-CLIENT-CHANNEL-ID` is optional. When present and the result isn't `signed_in`, the
  scheduler pushes an `external-service/signin` popup to that channel (FR-011) using the same
  `Api-Key` value as the caller's own identity, and re-checks once if the popup succeeds. Both
  headers are auto-forwarded when this endpoint is reached over MCP and are stripped from the
  MCP tool's input schema (`src/api/mcp/server.py::_strip_auto_forwarded_headers`).
- Changes no scheduler or DIAL state (FR-001) beyond that best-effort popup push; safe to call
  speculatively before any action.

## Response — 200

```json
{
  "service_id": "dial-native",
  "signed_in": false,
  "state": "not_signed_in",
  "detail": "You are not signed in to dial-native. Open the DIAL Scheduler application in DIAL Chat and sign in to it, then retry."
}
```

`state` is always one of `"signed_in" | "not_signed_in" | "unknown"` — this is the field a
caller (assistant or, eventually, a UI) MUST branch on. `signed_in` is a convenience derived
from `state == "signed_in"`; do not use it to distinguish `not_signed_in` from `unknown`.
`detail` is free text for direct display/relay — never parsed by a machine consumer (this is
the stability boundary from FR-010/data-model.md).

A `200` is returned for all three states, including `unknown` — this endpoint reports what it
found, including "I couldn't tell," rather than raising, because it has no side effect to
protect (contrast with the create/update precondition below, which does raise).

## Response — 422

Returned when `service_id` is missing — ordinary FastAPI/Pydantic query-validation behavior,
unchanged from every other endpoint.

## Popup delivery (FR-011/FR-012)

This response's `service_id` is the same value passed as `params.url`'s scope when a popup is
pushed to the caller's client channel — see research.md §5 for the exact mechanism, and why the
interact call authenticates as the caller, not the scheduler. The popup path is best-effort: a
failed push (no channel, DIAL Core unreachable, malformed response) never changes this
endpoint's response beyond what FR-002/FR-004 already specify — it only ever adds a
re-check-on-success, never a new failure mode.

---

# Contract (provided): Sign-In Precondition on Schedule Create/Update

Applies to the existing `POST /v1/schedules` and `PUT /v1/schedules/{id}` endpoints in
`src/api/v1/endpoints/schedules.py`. No new route; this modifies their behavior.

## Behavior

Before any repository write, the handler resolves `state` for the request body's `service_id`
(same probe as the status endpoint, via `SignInService.require`, given the same optional
`X-DIAL-CLIENT-CHANNEL-ID` and the caller's own `Api-Key` — so a channel'd caller gets the same
popup-then-retry behavior here as on the status endpoint, before the write is attempted):

- `state == "signed_in"` → proceeds exactly as before this feature.
- `state == "not_signed_in"` → **HTTP 403**, no schedule created/modified.
- `state == "unknown"` → **HTTP 502**, no schedule created/modified.

## Response — 403 (not signed in)

The scheduler runs on `DIALApp` (AiDial SDK), whose `HTTPException` handler uses `detail` as
the response body directly — unlike vanilla FastAPI, there is no outer `{"detail": ...}`
wrapper:

```json
{
  "service_id": "dial-native",
  "state": "not_signed_in",
  "detail": "You are not signed in to dial-native. Open the DIAL Scheduler application in DIAL Chat and sign in to it, then retry this request."
}
```

The assistant is expected to relay the `detail` string to the user verbatim (FR-003); the
`service_id`/`state` fields are for a future structured consumer (FR-007).

## Response — 502 (undetermined)

```json
{
  "service_id": "dial-native",
  "state": "unknown",
  "detail": "Could not determine sign-in status for dial-native. This is not necessarily a missing sign-in; verify the service id and retry."
}
```

`unknown` covers every outcome that is not a definite sign-in/not-signed-in answer: a DIAL
transport failure/outage, an unrecognized `service_id` (not present in the application's
`external_services`), a malformed response body, or — most importantly — `app_level_auth_status
!= "SIGNED_IN"` (an administrator has not approved this service for the application). The
`detail` text is written to avoid implying a missing user sign-in in any of these cases, since
signing in would not fix any of them.

Distinct `state` value from the 403 case (FR-008) — an assistant reading this MUST NOT tell the
user to sign in, since that would not resolve an outage (SC-004).

## Ordering

Request-body validation (Pydantic) and identity resolution (`get_user_id`) happen first, ahead
of this check — a malformed request or an unauthenticated caller never spends a DIAL round-trip
on the sign-in probe (see research.md §3).

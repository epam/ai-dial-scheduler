# Quickstart: Validating the Sign-In Status Signal

Validates the four user stories end-to-end once implemented. Assumes a running dial-scheduler
API instance (`uv run uvicorn src.api.app:app` or the local docker compose stack under `dial/`)
and a valid `Api-Key`. See `documentation/api_contract.md` for the general request conventions
already used by every other endpoint (auth header, base URL, error envelope).

## Prerequisites

- A DIAL instance reachable at `settings.dial_url`, with at least one test user known to be
  **not** connected to the `dial-native` external service, and (for the positive path) one
  known to already be connected.
- `uv sync` has been run; the scheduler API is up.

## Scenario 1 — Status check, not signed in (User Story 1, SC-001)

```
GET {base_url}/v1/sign-in/status?service_id=dial-native
Api-Key: {api_key of the not-signed-in user}
```

**Expect**: `200` with `state: "not_signed_in"`, `signed_in: false`, `service_id: "dial-native"`,
and a non-empty `detail`.

## Scenario 2 — Status check, signed in

```
GET {base_url}/v1/sign-in/status?service_id=dial-native
Api-Key: {api_key of the signed-in user}
```

**Expect**: `200` with `state: "signed_in"`, `signed_in: true`.

## Scenario 3 — Status check, DIAL unreachable (SC-004)

Point `DIAL_URL` at an address that refuses connections (or stop the local DIAL container), then
repeat Scenario 1's request.

**Expect**: `200` with `state: "unknown"` — distinct from `not_signed_in` — and a `detail` that
describes an outage, not a missing sign-in. Restore `DIAL_URL` afterward.

## Scenario 4 — Create schedule blocked (User Story 2, SC-002)

```
POST {base_url}/v1/schedules
Api-Key: {api_key of the not-signed-in user}
Content-Type: application/json

{
  "display_name": "test",
  "service_id": "dial-native",
  "trigger": { "trigger_type": "cron", "cron_expression": "0 * * * *" },
  "properties": { "target_type": "chat_completion", "payload": { "model": "gpt-4o" } }
}
```

**Expect**: `403`; the body itself (not nested under a `detail` key — `DIALApp` uses `detail`
as the response body directly) has `state == "not_signed_in"` and `service_id ==
"dial-native"`. Confirm via `GET /v1/schedules` that no schedule was created.

## Scenario 5 — Update schedule blocked

Using an existing schedule id owned by a user not signed in to its `service_id`:

```
PUT {base_url}/v1/schedules/{schedule_id}
Api-Key: {api_key of the not-signed-in user}
Content-Type: application/json

{ ...same body shape as Scenario 4... }
```

**Expect**: `403` with the same body shape as Scenario 4. Confirm via `GET
/v1/schedules/{schedule_id}` that the schedule is unchanged.

## Scenario 6 — MCP tool exposure (contract check)

With the MCP server mounted, list available tools and confirm `get_sign_in_status` is present
(and that its exposed input schema has no `Api-Key` parameter — it is auto-forwarded, per
`src/api/mcp/server.py`'s existing header-stripping behavior). Invoking it should return the
same payload as Scenario 1/2.

## Scenario 7 — Interactive popup via client-channel (User Story 4, SC-006)

Requires a subscribed client channel: `POST {dial_url}/v1/ops/client-channel/subscribe` with
`Authorization: Bearer {a real user token}` (kept open), which returns
`X-DIAL-CLIENT-CHANNEL-ID` on the response.

```
GET {base_url}/v1/sign-in/status?service_id=dial-native
Api-Key: {api_key of the not-signed-in user}
X-DIAL-CLIENT-CHANNEL-ID: {the subscribed channel id}
```

**Expect**: an `external-service/signin` RPC event (`params.url` naming `dial-native`'s scope)
is delivered on the subscribed stream within a few seconds. Reporting it back via
`POST {dial_url}/v1/ops/client-channel/report` with `{"result": "success"}` (after the user's
offline credentials are actually connected) makes a repeat of this request return
`state: "signed_in"`; reporting `{"result": "denied"}` leaves it `not_signed_in`, identical to
Scenario 1. **Note**: the `Api-Key` used here must be the same identity that subscribed — DIAL
Core resolves the channel by the caller's own bucket (research.md §5); the scheduler's own
service key cannot address another user's channel.

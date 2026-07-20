# Contract (consumed): DIAL External-Service OBO Credentials

The scheduler **consumes** this DIAL endpoint from the worker before each schedule execution.

## Request

```
POST {dial_url}/v1/ops/external-service/obo-credentials
Api-Key: {scheduler service key = settings.dial_api_key}
Content-Type: application/json

{
  "url": "applications/{scheduler_app_id}/external_services/{service_id}",
  "owner_user_id": "{schedule.created_by}"
}
```

- `scheduler_app_id` — from `settings.scheduler_app_id` (default `"dial_scheduler"`).
- `service_id` — from the schedule row (`"dial-oauth"` for migrated/legacy schedules).
- `owner_user_id` — the schedule's stored `created_by` (the user subject for new schedules).
- The `Api-Key` here authenticates the OBO request itself and is **never** forwarded to the target.

## Response — success (2xx)

For an API_KEY-type service (e.g. `dial-api-key`):

```json
{
  "header_name": "Api-Key",
  "header_value": "<per-user credential>"
}
```

For an OAUTH-type service (e.g. `dial-oauth`) — **confirmed against a live `dial-core`
(v0.46.0-dev.35)**:

```json
{
  "header_name": "Authorization",
  "header_value": "Bearer <token>",
  "expires_at": 1784124158
}
```

The worker applies `{header_name: header_value}` to the outgoing target request and records the
run as `success`/`error` based on the target call. `OboCredentials` models only `header_name` and
`header_value`; the extra `expires_at` field is silently dropped by Pydantic's default
`extra="ignore"` — acceptable since credentials are fetched fresh per run (no caching).

## Response — failure ("credential not found" / non-2xx / transport error)

Any non-2xx response, a body missing `header_name`/`header_value`, or a transport error is a
**credentials failure**. The worker:

1. Does NOT send the target request.
2. Records the run as `error` with a structured result:
   ```json
   {
     "stage": "credentials",
     "error_text": "obo credentials unavailable",
     "credentials_response": { "status": 404, "body": { "...": "verbatim DIAL response" } }
   }
   ```
3. Does NOT consume the schedule's target-request retry budget.

## Client method

`DialClient.get_obo_credentials(request: OboCredentialsRequest) -> OboCredentials` in
`src/lib/clients/dial_client.py`, reusing the worker's shared `httpx.AsyncClient`. Raises a typed
error (e.g. `DialServiceError` or a new `OboCredentialsError` carrying the response) on failure so
`jobs.py` can persist the credentials-lookup response.

## Security

- `header_value` is a secret: carried as `SecretStr`, never logged, never persisted.
- The failure `credentials_response` contains DIAL's error payload only (no issued credential).

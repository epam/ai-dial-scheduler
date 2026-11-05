# Contract: Readiness Endpoint

## `GET /ready`

Reports whether the API service is ready to serve traffic, gated on database reachability.
Registered at the application root (no `/v1` prefix), alongside the SDK's liveness `/health`.

- **Auth**: none (intended for orchestrator probes).
- **Request**: no parameters, no body.
- **Idempotent / safe**: yes; performs a read-only `SELECT 1`.

### Responses

#### 200 OK — ready

```json
{
  "status": "ready",
  "database": "ok"
}
```

#### 503 Service Unavailable — not ready

Returned when the database is unreachable, errors, or the check exceeds
`readiness_db_timeout_seconds`.

```json
{
  "status": "not_ready",
  "database": "unavailable"
}
```

### Behavior contract

| # | Given | When | Then |
|---|-------|------|------|
| 1 | DB reachable | `GET /ready` | `200`, body `{status: ready, database: ok}` |
| 2 | DB unreachable / errors | `GET /ready` | `503`, body `{status: not_ready, database: unavailable}`; service stays up |
| 3 | DB check exceeds timeout | `GET /ready` | `503` within ~`readiness_db_timeout_seconds`; no indefinite hang |
| 4 | DB recovers after being down | `GET /ready` polled | transitions `503 → 200` with no restart |

### Notes

- The response model is the Pydantic v2 `ReadinessResponse` (see `data-model.md`), so this contract is
  reflected in the auto-generated OpenAPI document.
- The existing liveness `/health` endpoint (provided by `DIALApp(add_healthcheck=True)`) is unchanged
  and does **not** check the database.

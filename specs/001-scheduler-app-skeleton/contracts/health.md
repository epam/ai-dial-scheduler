# Contract: Health Check Endpoint

**Version**: 1.0.0 | **Feature**: `001-scheduler-app-skeleton` | **Date**: 2026-06-09

---

## Overview

The API service exposes a single health check endpoint registered automatically by aidial-sdk when `add_healthcheck=True` is passed to `DIALApp`. No custom OpenAPI spec is added for this endpoint; it is intentionally excluded from developer-facing API docs.

---

## Endpoint

### `GET /health`

Returns the operational status of the API service.

**Implementation note**: Registered by `DIALApp(add_healthcheck=True)` in `src/api/app.py`. This is the only route exposed by the API in the skeleton.

#### Request

No request body. No query parameters. No authentication required.

```
GET /health HTTP/1.1
Host: <api-host>:<port>
```

#### Success Response

**HTTP 200 OK**

```json
{
  "status": "ok"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | Always `"ok"` when the service is running |

**Content-Type**: `application/json`

#### Error Responses

The endpoint does not perform downstream dependency checks (database, worker liveness). A 200 response means only that the API process is alive and accepting connections.

If the API process is not running or not yet ready, the TCP connection will be refused (no HTTP response).

---

## Logging Behaviour

Uvicorn access log entries for `GET /health` are suppressed by aidial-sdk's `PathFilter` to prevent log spam from health check polling (Kubernetes readiness/liveness probes).

---

## Breaking Change Policy

Any change to the response schema (field rename, removal, type change) or the HTTP status code constitutes a **MAJOR** version bump of this contract. Adding optional fields is a **MINOR** bump.

---

## Validation Checklist

- [ ] `GET /health` returns HTTP 200
- [ ] Response body is `{"status": "ok"}`
- [ ] `Content-Type` header is `application/json`
- [ ] Health probe does not appear in uvicorn access logs
- [ ] Endpoint is reachable before any database connection is established (it does not depend on the DB)

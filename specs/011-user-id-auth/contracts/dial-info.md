# Contract: DIAL User Info Call

**Direction**: aidial-scheduler (API service) → DIAL platform (outbound)

**Owner component**: `src/lib/clients/dial_client.py` → `DialClient.get_user_info`

## Request

- **Method**: `GET`
- **URL**: `{settings.dial_url}/v1/info`
- **Headers**:
  - `Api-Key: <caller's Api-Key from the incoming request>` (header name = `API_KEY_HEADER`)
- **Body**: none
- **Timeout**: `settings.http_timeout_seconds`
- **Transport**: shared `app.state.app_client` (`httpx.AsyncClient`)

## Response (consumed)

- **Success**: HTTP `2xx` with a JSON object body. Only the fields referenced by
  `settings.user_id_path_list` are read. Example:

  ```json
  {
    "userClaims": {
      "mail": ["user@example.com"]
    },
    "project": "project-alpha"
  }
  ```

- **Rejected credential**: HTTP `401` or `403` → treated as "identity not resolved"
  (`UserIdNotResolvedError`).
- **Other non-2xx** (`5xx`, `404`, etc.) → `DialServiceError`.
- **Transport error** (timeout, connection refused, DNS) → `DialServiceError`.
- **2xx but non-JSON / malformed body** → parsed as no usable identity →
  `UserIdNotResolvedError` (no candidate path can resolve).

## Client behavior contract

| Condition | `DialClient.get_user_info` outcome |
|-----------|------------------------------------|
| 2xx + JSON object | returns `dict[str, Any]` |
| 401 / 403 | raises `UserIdNotResolvedError` |
| other non-2xx | raises `DialServiceError` |
| transport error | raises `DialServiceError` |
| 2xx non-JSON | returns `{}` (so extraction yields no identity → 401 upstream) |

Secrets (the `Api-Key` value) MUST NOT appear in any log line emitted by this client.

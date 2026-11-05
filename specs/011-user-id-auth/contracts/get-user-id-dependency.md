# Contract: `get_user_id` FastAPI Dependency

**Owner component**: `src/api/dependencies/auth.py`

**Exposed alias**: `UserIdDep = Annotated[str, Depends(get_user_id)]`

Applied to all six schedule / schedule-run route handlers. Its presence adds an `Api-Key`
header requirement and `401` / `502` failure responses to each endpoint's OpenAPI.

## Inputs

- `request: Request` — to read the `Api-Key` header and `app.state.app_client`.
- `dial_client: DialClient` — via `Depends(get_dial_client)`, which wraps
  `request.app.state.app_client` + `settings.dial_url`.
- `settings` — for `user_id_path_list` (read inside the dependency or the client).

## Output

- Returns a non-empty `str` user id on success.

## Behavior

```
1. api_key = request.headers.get(API_KEY_HEADER)
2. if api_key is missing or empty/whitespace:
       log auth_rejected reason="missing_api_key"; raise HTTPException(401)
3. try:
       info = await dial_client.get_user_info(api_key)
   except UserIdNotResolvedError:
       log auth_rejected reason="dial_rejected"; raise HTTPException(401)
   except DialServiceError:
       log auth_failed reason="dial_unavailable"; raise HTTPException(502)
4. user_id = extract_user_id(info, settings.user_id_path_list)
5. if user_id is None:
       log auth_rejected reason="no_identity"; raise HTTPException(401)
6. return user_id
```

## Response contract per outcome

| Outcome | HTTP status | Notes |
|---------|-------------|-------|
| Valid key, identity resolved | route's normal status (200/201) | `user_id` forwarded to service |
| Missing/empty `Api-Key` | `401` | no DIAL call, no data access |
| DIAL rejects key (401/403) | `401` | no data access |
| No candidate path yields identity | `401` | no data access |
| DIAL transport/upstream error | `502` | no data access |

## Logging (no secrets)

Every rejection emits one structured `logger` entry with a `reason` code from
{`missing_api_key`, `dial_rejected`, `no_identity`, `dial_unavailable`}. The `Api-Key` value
and the full DIAL body are never logged.

## Test obligations (Test-First)

- Success returns resolved id and forwards it to the (mocked) service.
- Each of the four rejection branches returns the mapped status and performs **no** service
  call.
- Existing endpoint unit tests override `get_user_id` to a fixed id and assert that id reaches
  the service (replacing prior `"mock_user_id"` assertions).
- Integration test: two different mocked identities see disjoint schedule data.

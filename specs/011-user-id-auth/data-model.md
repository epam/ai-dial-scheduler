# Phase 1 Data Model: User Identity Authentication

This feature introduces **no database schema changes**. It adds a configuration value, two
domain exceptions, and transient runtime values. The persisted `created_by` / owner column on
existing schedule rows now receives the resolved identity instead of the mock constant — the
column itself is unchanged.

## Configuration

### `Settings.user_id_path_list`

- **Location**: `src/lib/settings.py` → `Settings`
- **Type**: `list[str]`
- **Default**: `["userClaims.mail.0", "project"]`
- **Meaning**: Ordered list of candidate paths into the DIAL `GET /v1/info` JSON response. The
  first path that resolves to a non-empty string is the caller identity.
- **Validation**: Standard pydantic-settings parsing. Empty list is permitted by the type but
  would mean "no identity can ever be resolved" → every request 401; the non-empty default is
  the supported configuration.
- **Override**: Environment variable `USER_ID_PATH_LIST` (JSON array), consistent with existing
  pydantic-settings env handling.

## Transient Runtime Values (not persisted)

### Caller Identity (`user_id`)

- **Type**: non-empty `str`
- **Source**: extracted from the DIAL `/v1/info` response per request
- **Lifetime**: request scope only; flows from `get_user_id` → endpoint → service layer
- **Invariants**: non-empty after `str()` + `strip()`; if it cannot be produced, no identity
  value exists and the request is rejected (no fallback to a default/mock id)

### DIAL User Info Response

- **Type**: parsed JSON object (`dict[str, Any]`)
- **Source**: body of `GET {dial_url}/v1/info`
- **Usage**: input to the path-extraction helper; not stored
- **Shape**: opaque/variable; only the configured candidate paths are read. Example relevant
  fields for the defaults: `userClaims.mail` (array of strings) and `project` (string).

## Path-Extraction Contract (pure helper)

`extract_user_id(data: Mapping[str, Any], paths: list[str]) -> str | None`

- Split each path on `.`. For each segment:
  - all-digit segment → list index (must index a `list`/`Sequence`, in range)
  - otherwise → mapping key (must index a `Mapping`)
- On any miss or type mismatch, abandon the current path and try the next.
- A path "yields" a value only if the terminal value coerces to a non-empty, non-whitespace
  `str`. Return the first such value; return `None` if no path yields one.

| Path | Example response | Result |
|------|------------------|--------|
| `userClaims.mail.0` | `{"userClaims": {"mail": ["a@b.com"]}}` | `"a@b.com"` |
| `userClaims.mail.0` then `project` | `{"userClaims": {"mail": []}, "project": "proj-1"}` | `"proj-1"` (fallback) |
| `userClaims.mail.0` then `project` | `{"userClaims": {}}` | `None` → 401 |
| `userClaims.mail.0` | `{"userClaims": {"mail": ["   "]}}` | skipped (whitespace) → next/`None` |

## Exceptions

Added to `src/lib/exceptions/errors.py` (subclasses of existing `SchedulerError` base):

- **`UserIdNotResolvedError`**: DIAL responded successfully but no candidate path produced a
  non-empty identity (or the key was rejected). Mapped by the dependency to **401**.
- **`DialServiceError`**: DIAL transport failure or unexpected non-2xx response. Mapped by the
  dependency to **502**.

(The dependency itself raises `fastapi.HTTPException`; these domain errors are raised by the
`lib/` layer — `DialClient` — and translated at the `api/` boundary, keeping `lib/` free of
FastAPI imports.)

## Affected Endpoints (identity now required)

| Method | Path | Service call (owner arg) |
|--------|------|--------------------------|
| POST   | `/v1/schedules/` | `service.create(request, user_id)` |
| PUT    | `/v1/schedules/{schedule_id}` | `service.update(schedule_id, request, user_id)` |
| GET    | `/v1/schedules/` | `service.list(user_id, paginator)` |
| GET    | `/v1/schedules/{schedule_id}` | `service.get(schedule_id, user_id)` |
| GET    | `/v1/schedules/{schedule_id}/runs` | `service.list_runs(schedule_id, user_id, paginator)` |
| GET    | `/v1/schedules/{schedule_id}/runs/{run_id}` | `service.get_run(schedule_id, user_id, run_id)` |

No service-layer signatures change; only the value passed for the owner argument changes from
`MOCK_USER_ID` to the resolved `user_id`.

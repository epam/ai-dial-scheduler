# Phase 1 Data Model: Per-User Credentials

## Modified: `Schedule` (ORM — `src/lib/models/schedule.py`)

| Field | Type | Notes |
|-------|------|-------|
| `service_id` | `str` (VARCHAR, NOT NULL) | **NEW.** External service whose OBO credentials the run uses. No DB default; supplied by API/MCP for new rows, backfilled to `"dial-oauth"` for pre-existing rows by migration. |

All existing columns (`id`, `display_name`, `created_by`, `trigger`, `kwargs`,
`schedule_metadata`) unchanged. `created_by` continues to hold the owner identity; going forward
it is the subject (`sub`) resolved via the updated `user_id_path_list`.

### Migration: `add_service_id_to_schedules`

1. `add_column("schedules", Column("service_id", sa.String(), nullable=True))`
2. Backfill: `UPDATE schedules SET service_id = 'dial-oauth' WHERE service_id IS NULL`
   (populates **existing** rows only; not a server default).
3. `alter_column("schedules", "service_id", nullable=False)`

`downgrade()` drops the column. No index required (not a query filter).

## Modified: `ScheduleRequest` / `ScheduleResponse` / `ScheduleListItem` (`src/lib/schemas/schedule.py`)

| Schema | Change |
|--------|--------|
| `ScheduleRequest` | **NEW** `service_id: str` (required; non-empty validator, mirrors `display_name`). Applies to create and update. |
| `ScheduleResponse` | **NEW** `service_id: str`. |
| `ScheduleListItem` | **NEW** `service_id: str` (summary convenience). |

`service_id` is a top-level field, **not** inside `ScheduleProperties` (it identifies the
credential source, not a target-call property).

## Modified: `RunContext` (`src/lib/schemas/run.py`)

| Field | Type | Notes |
|-------|------|-------|
| `auth_header_name` | `str` | **NEW, required.** OBO-provided outgoing auth header name. No default — a `RunContext` cannot be constructed without it, so an executor can never run with a malformed empty-named header. |
| `auth_header_value` | `SecretStr` | **NEW, required.** OBO-provided value; never logged/persisted. |

Set by `jobs.py` after a successful OBO fetch, before the executor runs. Both fields are required
(not defaulted) precisely because there is no safe placeholder value for a credential — every
call site (executors, retry policy, and all tests) must supply real values.

## New: credentials schemas (`src/lib/schemas/credentials.py`)

```text
OboCredentialsRequest:
    url: str            # "applications/{scheduler_app_id}/external_services/{service_id}"
    owner_user_id: str  # the schedule's created_by

OboCredentials:
    header_name: str
    header_value: SecretStr

RunError:              # structured, distinguishable run-failure result
    stage: Literal["credentials", "target"]
    error_text: str
    credentials_response: dict[str, Any] | None = None   # status/body of a failed OBO lookup
```

## State / behavior transitions

Schedule run lifecycle (worker), credential step inserted before target execution:

```
create_in_progress (status=in_progress)
        │
        ▼
resolve owner_user_id + service_id  ── schedule row missing/lookup failed ──► mark_credentials_error(
        │                                                                   RunError(stage="credentials", ...))
        │                                                                   [terminal]
        ▼
fetch OBO credentials ─────────── failure/not-found ──────► mark_credentials_error(
        │                                                     RunError(stage="credentials",
        │                                                       credentials_response=<resp>))  [terminal,
        │                                                       target request NOT sent, no retries]
        ▼ success (header_name/value)
execute_with_policy (target request, retries apply here)
        │                         │
   success                    failure
        ▼                         ▼
mark_success(result)        mark_error(error_text)  # plain {"error_text": ...}, no "stage" key
```

`ScheduleRunRepository` exposes two distinct failure-persistence methods: `mark_credentials_error`
(new, stores the structured `RunError` with `stage="credentials"`) and the pre-existing
`mark_error` (unchanged, stores the plain `{"error_text": ...}` shape for target-request
failures). A run is distinguishable as a credentials failure by the *presence* of a `stage` key in
`result`; target failures never have one. `RunError` models `stage: Literal["credentials",
"target"]`, but only the `"credentials"` value is currently persisted — target failures were
deliberately left on the pre-existing `mark_error` shape (lower risk to existing call sites);
distinguishability (FR-010) still holds via the `stage` key's presence/absence.

## Validation rules

- `service_id` MUST be non-empty on API create/update (422 otherwise).
- OBO `url` is composed as `applications/{scheduler_app_id}/external_services/{service_id}`, with
  both components percent-encoded (`urllib.parse.quote(..., safe="")`) before interpolation so a
  `service_id` containing special characters cannot produce a malformed request URL.
- On success, both `header_name` and `header_value` MUST be present or the fetch is treated as a
  credentials failure.
- `auth_header_value` MUST NOT appear in logs or persisted run results.
- Any `httpx.HTTPError` raised while calling the OBO endpoint (not only transport errors) is
  mapped to `OboCredentialsError` and therefore always recorded as a credentials-stage failure —
  never silently attributed to the target request.

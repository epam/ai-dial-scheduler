# Data Model: Scheduler MCP Sign-In Status Signal

No database tables, ORM models, or migrations are introduced by this feature — sign-in state is
computed live against DIAL on every call and nothing is persisted (see spec.md Assumptions).
The entities below are Pydantic-level contracts, not storage entities.

## SignInStatus

The outcome of checking one user's connection to one external service at a point in time.
Corresponds to spec.md's "Sign-In Status" key entity.

| Field        | Type                                          | Notes |
|--------------|------------------------------------------------|-------|
| `service_id` | `str`                                          | Stable identifier of the external service checked (e.g. `dial-native`); echoes the input, present so a caller never has to remember what it asked for. |
| `signed_in`  | `bool`                                          | `True` only when `state == "signed_in"`. Convenience field for simple truthy checks; MUST NOT be used to distinguish `not_signed_in` from `unknown` — use `state` for that (FR-002). |
| `state`      | `Literal["signed_in", "not_signed_in", "unknown"]` | The machine-readable field required by FR-002/FR-004. `unknown` means the check itself could not be completed (DIAL unreachable or returned an unexpected status) — never conflated with `not_signed_in` (FR-004, SC-004). |
| `detail`     | `str`                                          | Human-readable reason, safe to relay verbatim to the user (FR-003). Free text — not a stable contract; MUST NOT be parsed by any consumer (FR-010). |

**Validation rules**: `service_id` non-empty. `signed_in` is a derived, not independently
settable, value (computed as `state == "signed_in"` at construction). No relationships to other
entities; not persisted; no state transitions (each instance is a snapshot, immediately
discarded after the response is sent).

## ExternalServiceAuthStatus (internal client-level DTO, not exposed on the API)

Raw shape `DialClient.get_external_service_auth_status` parses out of `GET
/v1/applications/{app_id}`'s `external_services.{service_id}.auth_settings` object — DIAL's own
representation, internal to `SignInService._probe`, never returned as-is to a caller.

| Field                      | Type                                    | Notes |
|-----------------------------|------------------------------------------|-------|
| `user_level_auth_status`   | `Literal["SIGNED_IN", "SIGNED_OUT", "FAILED"]` | DIAL Core's own status enum for the identity that authenticated the request (the caller's forwarded `Api-Key`). |
| `app_level_auth_status`    | `Literal["SIGNED_IN", "SIGNED_OUT", "FAILED"]` | Whether an administrator has approved this service for the scheduler application; independent of `user_level_auth_status`. |

`SignInService._probe` maps these two fields to `SignInStatus.state` per research.md §2:
`app_level_auth_status != "SIGNED_IN"` → `unknown`; else `user_level_auth_status == "SIGNED_IN"`
→ `signed_in`; else `not_signed_in`.

## ExternalService (conceptual, not a new persisted model)

Spec.md's "External Service" key entity already exists in the codebase as the `service_id`
string field on `Schedule`/`ScheduleRequest` (added in feature 026) plus the
`EXTERNAL_SERVICE_URL_TEMPLATE` constant that combines it with `settings.scheduler_app_id` to
address DIAL's OBO endpoint. This feature introduces no new model or column for it — it reuses
the existing identifier verbatim as the `service_id` input/output of `SignInStatus`.

## SignInRequiredError (exception, not a schema)

Raised by `SignInService.require()` when `state != "signed_in"`. Carries:

| Attribute        | Type                                | Notes |
|------------------|--------------------------------------|-------|
| `service_id`     | `str`                                | Same identifier as `SignInStatus.service_id`. |
| `state`          | `Literal["not_signed_in", "unknown"]` | Never `signed_in` — `require()` only raises on failure. |
| `detail`         | `str`                                | Same free-text reason as `SignInStatus.detail`. |

The create/update endpoint handlers catch this and translate `state == "not_signed_in"` to
HTTP 403 and `state == "unknown"` to HTTP 502, each with a `detail` body shaped like
`SignInStatus` (FR-007).

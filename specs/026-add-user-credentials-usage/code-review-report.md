# Code Review Report — 026-add-user-credentials-usage

- **spec-dir**: `specs/026-add-user-credentials-usage/`
- **Date**: 2026-07-14
- **Mode**: Fresh Review
- **Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `contracts/obo-credentials.md`
- **Scope**: uncommitted working-tree diff on branch `026-add-user-credentials-usage`

## Summary

**Approved with minor changes.** The feature is implemented cleanly and faithfully to
the spec: the shared `dial_api_key` is fully removed from all three executors, per-user OBO
credentials flow through `RunContext`, credential failures are terminal (target request never
sent, retry policy never invoked), and no secret is logged or persisted. Test coverage is
thorough and genuinely adapted (not papered over). A small number of edge-case and
consistency improvements are recommended; none block merge.

## Critical Issues

None.

## Improvements

### I1 — chat_completion path ignores the OBO `header_name` (Status: Open)
**File**: `src/worker/executors/chat_completion.py:32-45`

The REST and SSE executors apply the OBO credentials generically as
`{header_name: header_value}` (per FR-007). The chat_completion executor instead discards
`ctx.auth_header_name` entirely and always feeds the value into `AsyncAzureOpenAI(api_key=...)`,
which emits the fixed `api-key` header. For an OAUTH-type service (`dial-oauth`, the default and
the migration backfill value) DIAL returns `Authorization: Bearer <token>`; the executor strips
`Bearer ` and sends the raw token as `api-key`. This is a documented decision
(plan §5, research §3, "confirmed against live dial-core") and is unit-tested for both shapes,
so it is not necessarily a defect — but it is a real divergence from the "apply exactly the
returned header" contract and is the single most important thing to confirm end-to-end, because
it affects every migrated chat_completion schedule.

**Recommendation**: Confirm via the opt-in real-DIAL integration test that a `dial-oauth`
chat_completion run authenticates successfully. If DIAL ever requires the token on
`Authorization` for the chat path, switch to `AsyncAzureOpenAI(default_headers={header_name:
header_value})` (with `azure_ad_token`/placeholder key) rather than the `api-key` slot.

### I2 — Non-`OboCredentialsError` failures in the OBO block lose stage distinguishability (Status: Open)
**File**: `src/worker/jobs.py:107-123`

`_resolve_credentials` only catches `OboCredentialsError` around the OBO call. `DialClient.get_obo_credentials`
maps `httpx.HTTPStatusError` and `httpx.TransportError`, but other `httpx.HTTPError` subclasses
(`TooManyRedirects`, `InvalidURL`) and any unexpected error during response handling/`OboCredentials`
construction would propagate uncaught out of `_resolve_credentials`. They are then caught by the
generic `except Exception` in `_execute_scheduled_job_inner` and recorded via `_persist_error`
(the plain, stage-less `{"error_text": ...}` shape) — i.e. recorded as if it were a target-stage
failure, violating FR-010 for those (rare) cases.

**Recommendation**: In `DialClient.get_obo_credentials`, catch the broader `httpx.HTTPError`
instead of `httpx.TransportError`, or wrap the OBO block in `_resolve_credentials` to convert any
unexpected exception into a credentials-stage `RunError`.

### I3 — Duplicated persist helpers (DRY) (Status: Open)
**File**: `src/worker/jobs.py:29-65`

`_persist_success`, `_persist_error`, and `_persist_credentials_error` are three near-identical
session-open / call-repo / log-outcome blocks. Consider extracting a single helper parameterized
by the repository call (a small callable) to remove the repetition. Low priority.

## Minor Suggestions

### M1 — `RunContext` auth defaults are a footgun (Status: Open)
**File**: `src/lib/schemas/run.py:20-21`

`auth_header_name: str = ""` and `auth_header_value: SecretStr = SecretStr("")` allow a context to
be constructed without credentials. In the real flow `_resolve_credentials` always runs first, but
if an executor ever ran with the defaults, REST/SSE would emit a malformed empty-named header. The
defaults exist mainly for test convenience; consider making them required (and updating the few
non-credential test constructions) or adding a guard.

### M2 — Target-stage errors are not stored as `RunError(stage="target")` (Status: Open)
**File**: `src/lib/repositories/schedule_run_repository.py:40-48` vs `data-model.md`

`data-model.md` models both failure stages via `RunError` (`stage: Literal["credentials",
"target"]`), but target failures keep the legacy `{"error_text": ...}` shape via `mark_error`.
Distinguishability (FR-010, SC-005) still holds — a `stage` key is present only for credentials
failures — and the divergence is deliberately documented in the `mark_credentials_error` docstring.
Consider persisting `RunError(stage="target", ...)` for symmetry so an operator keys off one field.

### M3 — External-service URL template values are not URL-encoded (Status: Open)
**File**: `src/lib/constants.py:15`, `src/worker/jobs.py:102-104`

`EXTERNAL_SERVICE_URL_TEMPLATE.format(app_id=..., service_id=...)` interpolates raw values. Since
`service_id` is user-supplied (validated only as non-empty), a value with special characters would
produce a malformed `url` field. Low risk given expected values (`dial-oauth`), but worth a note.

## Intent Alignment

- **FR-001/002/003/004** (service_id required, migration backfill, MCP guidance): correctly
  implemented — required non-empty validator on `ScheduleRequest`, migration follows the
  nullable → backfill `dial-oauth` → NOT NULL sequence, endpoint docstrings/examples updated.
- **FR-005/006/012/013**: OBO request built with `applications/{scheduler_app_id}/external_services/{service_id}`
  and `owner_user_id = created_by`, owner/service read authoritatively from the `schedules` row via the
  new `get_by_id`. `user_id_path_list` updated to `["sub", "project"]`, `scheduler_app_id` default
  `dial_scheduler`. All correct.
- **FR-007/008**: OBO header replaces the shared key across REST/SSE generically (verified by the
  "obo-wins over stale Api-Key" test). Chat_completion applies value-only — see **I1**.
- **FR-009/010/011**: Credential failure marks the run error with the DIAL response, skips the
  target request, does not consume the retry budget (verified: `execute_with_policy` not called,
  `mark_error` not awaited on the credential path). Fetched fresh per run (no caching). Aligned,
  with the narrow gap noted in **I2**.
- **Security (plan Constraints)**: `header_value` is `SecretStr` in `OboCredentials`/`RunContext`,
  never logged (test asserts the token is absent from all logs), never persisted; the failure
  `credentials_response` carries only DIAL's error status/body. Solid.

## Checklist Pass

- **Correctness & Logic**: PASS (credential failure is terminal and pre-empts retries; no double
  persist on the credentials path). Edge gap noted in I2.
- **Type Safety**: PASS — full hints, Pydantic v2 models for cross-boundary data, `SecretStr` used
  correctly, no ad-hoc dicts across the worker→DIAL boundary.
- **Code Style & Conventions**: PASS — Google-style docstrings, no `print()`, `logger` used,
  imports ordered, line length within limits.
- **Architecture**: PASS — business logic in worker/service, repository pattern, DialClient reused
  from `src/lib`, credentials grouped in Pydantic models (2–3 param rule). Minor DRY note (I3).
- **Database & ORM**: PASS — no raw SQL in ORM paths, migration correct, `down_revision`
  `be7408b25155` correctly chains to the prior head.
- **Project Structure**: PASS — new files in the right places (`schemas/credentials.py`,
  migration, migration test mirrors under `tests/unit/migrations/`).
- **Testing**: PASS — happy path, credential-failure, missing-schedule, lookup-exception,
  persist-failure, retry-not-consumed, no-secret-in-logs, Bearer-strip for both service shapes,
  migration upgrade/downgrade all covered. Old shared-key tests genuinely re-adapted.
- **Security**: PASS — no secret logged/persisted; secret carried as `SecretStr`.

---

## Follow-up Review

- **Date**: 2026-07-14
- **Mode**: Verification
- **Scope**: the three fixes applied for I2, M1, M3, plus a sanity pass over the uncommitted working-tree diff vs HEAD.

### Summary

**Approved.** All three targeted fixes are correct and complete. Quality gates and the full unit suite pass on the current tree (516 passed; `ruff check`, `ruff format --check`, `mypy src` all clean). No regressions or new issues introduced by the fixes. Remaining open items (I1, I3, M2) are the previously documented, deliberately-deferred trade-offs and are unchanged.

### Verified Fixes

#### I2 — Non-`OboCredentialsError` failures in the OBO block (Status: Resolved)
**File**: `src/lib/clients/dial_client.py` (`get_obo_credentials`)

The narrow `except httpx.TransportError` was broadened to `except httpx.HTTPError`, so every request-side httpx failure (`TooManyRedirects`, `InvalidURL`, transport errors, etc.) is uniformly mapped to `OboCredentialsError`. The caller (`_resolve_credentials`) therefore always records a credentials-stage error and never falls through to the stage-less generic handler in `_execute_scheduled_job_inner` — closing the FR-010 gap.
- **Except-clause ordering is correct**: `except httpx.HTTPStatusError` (line ~55) precedes `except httpx.HTTPError` (line ~60). Since `HTTPStatusError` is a subclass of `HTTPError`, the specific handler (which preserves `status_code`/`response_body`) still wins for non-2xx responses; only genuinely status-less failures fall to the broad clause.
- **Test coverage present**: `test_non_transport_http_error_raises_obo_error` in `tests/unit/lib/clients/test_dial_client.py` drives `httpx.TooManyRedirects` (an `HTTPError` that is not a `TransportError`) and asserts `OboCredentialsError` is raised. Passes.

#### M1 — `RunContext` auth fields made required (Status: Resolved)
**File**: `src/lib/schemas/run.py`

`auth_header_name: str` and `auth_header_value: SecretStr` are now required (no `""` / `SecretStr("")` defaults), eliminating the empty-named-header footgun. A class docstring documenting the secret handling was added.
- **All call sites supply both fields**: a repo-wide grep for `RunContext(` shows every construction in `src/` (`src/worker/jobs.py`) and `tests/` (`test_retry.py`, `test_sse.py`, `test_rest.py`, `test_chat_completion.py`, `tests/integration/test_echo_endpoints.py`) now passes both fields explicitly. None rely on the removed defaults.
- **Field ordering is safe**: all call sites use keyword arguments, so placing the two required fields before the optional `effective_timeout` breaks no call site. (Field declaration order before an optional field is also valid Pydantic v2.)

#### M3 — External-service URL template values URL-encoded (Status: Resolved)
**File**: `src/worker/jobs.py` (`_resolve_credentials`), template in `src/lib/constants.py`

`settings.scheduler_app_id` and `schedule.service_id` are now passed through `urllib.parse.quote(..., safe="")` before interpolation into `EXTERNAL_SERVICE_URL_TEMPLATE`, preventing a malformed `url` field for values containing special characters.
- **Normal case unaffected**: underscore and hyphen are RFC 3986 unreserved characters, so `quote("dial_scheduler", safe="")` → `dial_scheduler` and `quote("dial-oauth", safe="")` → `dial-oauth`. The exact-body assertion in `tests/unit/worker/test_jobs.py:683` (`applications/dial_scheduler/external_services/dial-oauth`) still holds and passes.

### Sanity Pass on the Diff

No new problems introduced by the fixes. The imports added to `jobs.py` (`quote`) and `dial_client.py` (`OBO_CREDENTIALS_PATH`, `OboCredentialsError`, credentials schemas) are all used and correctly ordered. `_safe_body` helper is well-scoped. No secrets are logged or persisted on any of the touched paths.

### Checklist Pass (Follow-up)

- Correctness & Logic: PASS — I2 gap closed; except ordering correct.
- Type Safety: PASS — `mypy src` clean; required `SecretStr` field enforced.
- Style & Conventions: PASS — `ruff check` and `ruff format --check` clean.
- Testing: PASS — 516 unit tests pass, including the new I2 test and updated `RunContext` constructions.

# Quickstart / Validation Guide: Per-User Credentials

Validates that schedules execute with per-user OBO credentials and fail cleanly when credentials
are unavailable. See [data-model.md](./data-model.md) and [contracts/](./contracts/) for shapes.

## Prerequisites

- `uv sync`
- PostgreSQL reachable per `.env` (never read by code/tests directly).
- Settings: `dial_url`, `dial_api_key` set; `scheduler_app_id` defaults to `dial_scheduler`.

## Setup

```bash
uv run alembic upgrade head          # applies add_service_id_to_schedules
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

## Automated validation

```bash
uv run pytest                        # full suite (unit + integration)
uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml
```

Key tests that must pass (created test-first during `/speckit-implement`):

- **Migration**: existing schedule rows get `service_id = "dial-oauth"`; column is NOT NULL after upgrade.
- **Settings**: `scheduler_app_id == "dial_scheduler"`; `user_id_path_list == ["sub", "project"]`.
- **DialClient.get_obo_credentials**: builds `url = applications/{scheduler_app_id}/external_services/{service_id}`
  and `owner_sub`, sends `Api-Key`; parses `header_name`/`header_value`; maps non-2xx / missing
  fields / transport error to a credentials failure carrying the response.
- **Executors (rest/sse/chat_completion)**: outgoing request carries the OBO header and NO
  shared `Api-Key` from settings; OBO header overrides a same-named `extra_headers` entry.
  Covered for BOTH external-service auth types per `dial/core/config-template.json` —
  API_KEY (`Api-Key: <value>`) and OAUTH (`Authorization: Bearer <token>`).
- **Worker `jobs.py`**: success path injects credentials into `RunContext` then runs the target;
  credential failure marks run `error` with `stage="credentials"` and does NOT call the executor
  or consume retries; missing schedule row → credentials error.
- **Schedule API**: create/update without `service_id` → 422; response/list include `service_id`.

## Manual end-to-end validation

1. **Happy path** — start API + worker; a signed-in user creates a schedule with
   `service_id: "dial-oauth"` and a near-future `date` trigger. With a valid OBO stub returning
   `{"header_name": "Api-Key", "header_value": "user-token"}`, confirm:
   - the target request carries `Api-Key: user-token` (not the shared service key);
   - the run is `success` (`GET /schedules/{id}/runs`).

2. **Missing credentials** — for a user with no external-service sign-in, the OBO stub returns a
   non-2xx (e.g. 404). Confirm the run is `error`, `result.stage == "credentials"`,
   `result.credentials_response` holds the DIAL response, and no target request was sent.

3. **Legacy schedule** — a pre-existing schedule (email-style `created_by`, backfilled
   `service_id = "dial-oauth"`) executes; if DIAL cannot resolve the owner it is recorded as a
   credentials error (best-effort, no crash).

4. **Secret hygiene** — inspect logs and the persisted `result`: `header_value` must never appear.

## Real-DIAL integration (opt-in)

Requires the local `dial/docker-compose.yml` stack (DIAL core + Keycloak). The scheduler app and
its external services are defined in `dial/core/config-template.json` (`dial-oauth` = OAUTH,
`dial-api-key` = API_KEY).

```bash
# Sign the test user in to the dial-oauth external service and print their sub:
uv run python scripts/dial_sign_in.py            # or --dial-url / --keycloak-url overrides
```

Create a schedule whose `created_by` matches the printed `sub` and `service_id: "dial-oauth"`;
when it fires, the worker's OBO fetch returns real credentials and the target request runs as that
user. See `tests/integration/test_run_with_real_dial.py` (T036) and the README testing section.

## Expected outcomes (maps to Success Criteria)

- SC-001: no execution uses the shared key on the target request.
- SC-002: missing credentials → error run with the lookup response; no target request.
- SC-003: all pre-existing schedules associated with `dial-oauth` and still execute.
- SC-004: API create/update without `service_id` is rejected.
- SC-005: failed runs are attributable to credentials vs target via `result.stage`.

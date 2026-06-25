# Quickstart & Validation: Workload-Identity Database Authentication

This guide proves the feature end-to-end. Unit tests validate mode selection and wiring locally
(mocked cloud SDKs); the GCP/Azure scenarios are validated in their respective cloud environments.

## Prerequisites

- `uv sync` (installs new deps: `cloud-sql-python-connector[pg8000]`, `azure-identity`).
- For cloud scenarios: a deployed workload with the appropriate identity (GCP service account with
  Cloud SQL Client role + IAM DB user; or Azure managed identity granted Entra DB access). See
  `contracts/db-auth-config.md`.

## Scenario A — Backward compatibility (local, no cloud) · validates US3

```bash
export DB_AUTH_MODE=password DB_HOST=localhost DB_NAME=app DB_USER=app DB_PASSWORD=app
uv run pytest tests/unit/lib            # all green
uv run alembic upgrade head             # migrations run via psycopg2 as before
```

Expected: API and worker start and behave exactly as the prior release. Omitting `DB_AUTH_MODE`
defaults to `password`.

## Scenario B — Fail-fast validation · validates FR-005 / SC-005

```bash
export DB_AUTH_MODE=gcp_iam            # required GCP vars intentionally unset
uv run python -c "from src.lib.settings import get_settings; get_settings()"
```

Expected: `ValueError` naming `db_gcp_instance_connection_name, db_gcp_iam_user`. Repeat for
`azure_entra` (expects `db_azure_entra_user`).

## Scenario C — GCP IAM (deployed on GCP) · validates US1 / SC-001 / SC-004

Configure per `contracts/db-auth-config.md` (`gcp_iam`), deploy, then verify:

1. Startup log shows `Initializing a new DB engine with gcp_iam auth mode.` and **no** password in
   config/secrets.
2. API serves a DB-backed request (e.g. list schedules) successfully.
3. Worker registers and runs the heartbeat job (job store reachable via pg8000 connector).
4. `uv run alembic upgrade head` (run as the same workload identity) succeeds.
5. Leave running past one credential lifetime; confirm new connections still succeed (connector
   refresh) with no errors in logs.

## Scenario D — Azure Entra (deployed on Azure) · validates US2 / SC-002 / SC-004

Configure per `contracts/db-auth-config.md` (`azure_entra`), deploy, then verify:

1. Startup log shows `Initializing a new DB engine with azure_entra auth mode.` and no password.
2. API serves a DB-backed request successfully (async engine, fresh token per connect).
3. Worker heartbeat job runs (sync psycopg2 job store with `do_connect` token injection).
4. `uv run alembic upgrade head` succeeds under the managed identity.
5. Leave running past the token lifetime (≤60 min); confirm new connections still succeed (SDK
   token refresh) with no expiry errors.

## Secret-safety check · validates FR-008 / SC-006

```bash
# In any scenario, scan emitted logs — no token/password/URL secret may appear at any level.
```

## Quality gates (run before marking complete)

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml
```

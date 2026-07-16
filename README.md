# AIDIAL Scheduler

Backend scheduler for the DIAL platform. Runs as three services from a single Docker image, dispatched by the `SERVICE` environment variable.

## Components

- **API** — FastAPI application (via aidial-sdk) exposing a health check endpoint. Future features will add job scheduling routes.
- **Worker** — APScheduler `AsyncIOScheduler` process that executes scheduled jobs. Maintains a PostgreSQL-backed jobstore.
- **Migrations** — One-shot Alembic runner that applies schema migrations before the API and worker start.

## Prerequisites

- Docker ≥ 24 and Docker Compose v2
- `uv` (for running quality gates on the host)
- Ports `5432` and `5000` available

## Quickstart

```bash
cp .env.example .env
# Edit .env and fill in real values if needed
docker compose up --build
```

Expected startup sequence (completes within ~2 minutes):

1. `postgres` starts and passes its health check
2. `migrations` runs `alembic upgrade head` and exits `0`
3. `api` and `worker` start

## Verifying Health

```bash
curl -s http://localhost:5000/health
```

Expected response:

```json
{"status": "ok"}
```

HTTP 200.

## Verifying Readiness (database check)

```bash
curl -i http://localhost:5000/ready
```

Returns `200` with `{"status":"ready","database":"ok"}` when the database is reachable,
or `503` with `{"status":"not_ready","database":"unavailable"}` when it is not.
The service itself keeps running in the `503` case and recovers automatically once the DB
is reachable again. The timeout for the internal check is controlled by
`READINESS_DB_TIMEOUT_SECONDS` (default `2.0` seconds).

## Verifying Worker

```bash
docker compose logs worker   # should contain: "Starting worker."
docker compose ps worker     # STATUS column should show "running"
```

## Testing

Install dev dependencies first:

```bash
uv sync
```

### Unit tests

No running services required.

```bash
# All unit tests
uv run pytest tests/unit/

# Specific package
uv run pytest tests/unit/lib/
uv run pytest tests/unit/lib/models/

# With coverage
uv run pytest tests/unit/ --cov=src --cov-report=term-missing
```

### Integration tests

#### Automated (recommended)

`scripts/run-integration-tests.ps1` creates a temporary `scheduler_test` database, runs the
tests, and drops the database — even if tests fail.
Connection details are read from `.env` automatically.

```powershell
# Uses .env defaults (DB_HOST / DB_PORT / DB_USER / DB_PASSWORD)
.\scripts\run-integration-tests.ps1

# Override the exposed port if it differs from DB_PORT in .env
.\scripts\run-integration-tests.ps1 -DbPort 5433

# Pass extra pytest flags
.\scripts\run-integration-tests.ps1 -PytestArgs "-v -k test_schedule"
```

Requires `psql` on `PATH` (comes with any PostgreSQL client install).

#### Manual

Set `TEST_DB_URL` and run pytest directly. The suite creates and drops all
managed tables per test module; if `TEST_DB_URL` is not set the tests are
skipped, not failed.

```powershell
$env:TEST_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/test_scheduler"
uv run pytest tests/integration/ -v
```

### All tests

```bash
uv run pytest --cov=src --cov-report=term-missing
```

## Database authentication

The database connection (async engine, the APScheduler job store, and Alembic) authenticates
according to `DB_AUTH_MODE`. Four modes are supported; `password` is the default. See
`specs/022-db-workload-identity-auth/contracts/db-auth-config.md` for the full contract.

| Mode | Required env | Notes |
|------|--------------|-------|
| `password` *(default)* | `DB_PASSWORD` (+ `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`) | Static credentials. |
| `connection-string` | `DB_URL` | Verbatim SQLAlchemy URL. |
| `gcp_iam` | `DB_GCP_INSTANCE_CONNECTION_NAME`, `DB_GCP_IAM_USER` | Cloud SQL IAM via the workload's Google service account. Optional `DB_GCP_PRIVATE_IP=true`. No password stored. |
| `azure_entra` | `DB_AZURE_ENTRA_USER` | Microsoft Entra token via the workload's managed identity. Uses `DB_HOST`/`DB_NAME`/`DB_PORT` (shared defaults). Optional `DB_AZURE_MANAGED_IDENTITY_CLIENT_ID` for a user-assigned identity. No password stored. |

For `gcp_iam` and `azure_entra` the service uses the platform-provided workload identity to obtain
short-lived credentials that are refreshed automatically for new connections — no database password
is configured or stored. Missing required variables for the selected mode cause a fail-fast startup
error naming every missing field.

```dotenv
# GCP Cloud SQL IAM
DB_AUTH_MODE=gcp_iam
DB_GCP_INSTANCE_CONNECTION_NAME=my-proj:europe-west1:scheduler-pg
DB_GCP_IAM_USER=scheduler-sa@my-proj.iam
DB_GCP_PRIVATE_IP=true
DB_NAME=scheduler

# Azure Database for PostgreSQL — Microsoft Entra
DB_AUTH_MODE=azure_entra
DB_HOST=scheduler-pg.postgres.database.azure.com
DB_NAME=scheduler
DB_AZURE_ENTRA_USER=scheduler-identity
# DB_AZURE_MANAGED_IDENTITY_CLIENT_ID=<user-assigned-mi-client-id>  # optional
```

## Per-User Credentials (external-service sign-in)

Each schedule executes using the credentials of the user who created it, not a shared service
key. Every schedule carries a `service_id` identifying which DIAL external service (registered
in `application/external-services`, e.g. `dial-oauth` or `dial-api-key`) supplies those
credentials. Before each run, the worker requests on-behalf-of (OBO) credentials for the
schedule's owner and `service_id` from DIAL
(`POST /v1/ops/external-service/obo-credentials`) and applies the returned header to the outgoing
request — the shared `DIAL_API_KEY` is never sent to the target.

- **`service_id` is required** on `POST /v1/schedules/` and `PUT /v1/schedules/{id}`; requests
  without it are rejected with `422`. Use `"dial-oauth"` unless a different external service is
  required (this is also what MCP clients are instructed to use by default).
- **If the owner has not signed in** to the requested external service (or DIAL rejects the OBO
  lookup), the run is recorded as `error` with the credentials-lookup response in the result —
  the target request is never sent, and this does not consume the schedule's retry budget.
- Schedules that existed before this feature were backfilled with `service_id = "dial-oauth"` by
  migration `add_service_id_to_schedules`.

### Testing against a real DIAL sign-in (`scripts/dial_sign_in.py`)

To exercise a schedule end-to-end with real OBO credentials, a user must first sign in to the
`dial-oauth` external service. `scripts/dial_sign_in.py` automates this against the local
`dial/docker-compose.yml` stack (Keycloak + DIAL core): it obtains a Keycloak user token,
drives the login form to get an authorization code, and calls DIAL's
`POST /v1/ops/external-service/signin` for
`applications/dial_scheduler/external_services/dial-oauth`.

```bash
uv run python scripts/dial_sign_in.py
# uv run python scripts/dial_sign_in.py --dial-url http://localhost:8081 --keycloak-url http://localhost:8900
```

The script prints the token's `sub` claim — create a schedule whose owner resolves to that same
`sub` (via `USER_ID_PATH_LIST`) so the worker's OBO fetch can find the sign-in and resolve
credentials.

## Observability / OTLP

Both the API service and the background worker emit distributed traces, metrics, and structured
logs (with `trace_id` / `span_id` on every log record produced within an active span). Export to
an OTLP-compatible collector is controlled entirely by standard OpenTelemetry SDK environment
variables — no code changes are required.

Copy these into your `.env` (or set them in the container environment) to enable export:

```dotenv
# Enable OTLP export of traces and metrics
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_SERVICE_NAME=aidial-scheduler

# Optional: also export structured logs via OTLP
# OTEL_LOGS_EXPORTER=otlp

# Optional: expose Prometheus metrics scrape endpoint (port 9464)
# OTEL_METRICS_EXPORTER=prometheus

# Internal OpenTelemetry logger verbosity (default: ERROR)
# OTEL_LOG_LEVEL=ERROR
```

When none of the `OTEL_*_EXPORTER` variables are set the services start normally with
telemetry export inactive. Pointing the endpoint at an unreachable collector does **not**
crash the services — export failures are handled silently by background batch processors.

See `.env.example` for a commented reference of all supported variables.

## Deployment Env Setup — Migrations

The `migrations` service runs `alembic upgrade head` and exits. It only requires database connection variables.

Alternatively, the `api` and `worker` services can apply migrations themselves on boot by setting `RUN_MIGRATIONS_ON_START=true` (off by default), removing the need for a separate `migrations` service in environments where an init/job container is inconvenient.

| Variable | Default | Required | Description |
|---|---|----------|---|
| `SERVICE` | — | Yes      | Must be `migrations` |
| `DB_HOST` | `localhost` | No       | PostgreSQL host |
| `DB_PORT` | `5432` | No       | PostgreSQL port |
| `DB_NAME` | `app` | No       | Database name |
| `DB_USER` | `app` | Yes      | Database username |
| `DB_AUTH_MODE` | `password` | No       | Auth mode: `password` or `connection-string` |
| `DB_PASSWORD` | — | Yes      | Database password |
| `DB_URL` | — | Yes      | Full SQLAlchemy connection string |
| `DB_DRIVER` | `postgresql+asyncpg` | No       | SQLAlchemy driver dialect |
| `DIAL_URL` | `stub` | Yes      | Not used by migrations; any non-empty value is accepted |
| `DIAL_API_KEY` | `stub` | Yes      | Not used by migrations; any non-empty value is accepted |

## Deployment Env Setup — Api

The `api` service runs the FastAPI application.

| Variable | Default | Required | Description |
|---|---|---|---|
| `SERVICE` | — | Yes | Must be `api` |
| `RUN_MIGRATIONS_ON_START` | `false` | No | When truthy (`true`/`1`), run `alembic upgrade head` before the api starts; aborts startup on failure. Alternative to the standalone `migrations` service |
| `DIAL_URL` | — | Yes | Base URL of the DIAL platform |
| `DIAL_API_KEY` | — | Yes | API key for authenticating with DIAL |
| `PORT` | `5000` | No | HTTP port to listen on |
| `LOG_LEVEL` | `INFO` | No | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `APP_NAME` | `aidial-scheduler` | No | Application name reported in traces and logs |
| `ECHO_ENDPOINTS` | `false` | No | Log all incoming HTTP requests and responses |
| `MCP_ENABLED` | `true` | No | Enable the MCP endpoint |
| `MCP_PATH` | `/mcp` | No | URL path for the MCP endpoint |
| `USER_ID_PATH_LIST` | `["sub","userClaims.sub.0","project"]` | No | Ordered JSON paths used to extract the caller's user ID from DIAL claims |
| `SCHEDULER_APP_ID` | `dial_scheduler` | No | This app's DIAL application id, used to build the on-behalf-of credentials request (`applications/{SCHEDULER_APP_ID}/external_services/{service_id}`) |
| `READINESS_DB_TIMEOUT_SECONDS` | `2.0` | No | Timeout for the database check on `GET /ready` |
| **Database** | | | |
| `DB_HOST` | `localhost` | No | PostgreSQL host |
| `DB_PORT` | `5432` | No | PostgreSQL port |
| `DB_NAME` | `app` | No | Database name |
| `DB_USER` | `app` | Yes | Database username |
| `DB_AUTH_MODE` | `password` | No | Auth mode: `password` or `connection-string` |
| `DB_PASSWORD` | — | Yes | Database password |
| `DB_URL` | — | Yes | Full SQLAlchemy connection string |
| `DB_DRIVER` | `postgresql+asyncpg` | No | SQLAlchemy driver dialect |
| `DB_POOL_SIZE` | `10` | No | Connection pool size |
| `DB_MAX_OVERFLOW` | `20` | No | Max overflow connections above pool size |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | No | Recycle connections older than this many seconds |
| `DB_POOL_TIMEOUT_SECONDS` | `30` | No | Timeout waiting to acquire a connection from the pool |
| `DB_POOL_PRE_PING` | `true` | No | Test connections with a ping before use |
| `DB_ECHO` | `false` | No | Log all SQL statements |
| **HTTP Client** | | | |
| `API_HTTP_TIMEOUT_SECONDS` | `60.0` | No | Overall outbound request timeout |
| `API_HTTP_CONNECT_TIMEOUT_SECONDS` | `5.0` | No | TCP connection timeout |
| `API_HTTP_READ_TIMEOUT_SECONDS` | `60.0` | No | Socket read timeout |
| `API_HTTP_WRITE_TIMEOUT_SECONDS` | `60.0` | No | Socket write timeout |
| `API_HTTP_POOL_TIMEOUT_SECONDS` | `5.0` | No | Timeout waiting for a pooled connection |
| `API_HTTP_MAX_CONNECTIONS` | `200` | No | Max total HTTP connections |
| `API_HTTP_MAX_KEEPALIVE_CONNECTIONS` | `20` | No | Max keep-alive connections |

## Deployment Env Setup — Worker

The `worker` service runs the APScheduler background process that executes scheduled jobs.

| Variable | Default | Required | Description |
|---|---|---|---|
| `SERVICE` | — | Yes | Must be `worker` |
| `RUN_MIGRATIONS_ON_START` | `false` | No | When truthy (`true`/`1`), run `alembic upgrade head` before the worker starts; aborts startup on failure. Alternative to the standalone `migrations` service |
| `DIAL_URL` | — | Yes | Base URL of the DIAL platform |
| `DIAL_API_KEY` | — | Yes | API key for authenticating with DIAL |
| `LOG_LEVEL` | `INFO` | No | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `APP_NAME` | `aidial-scheduler` | No | Application name reported in traces and logs |
| `NOOP_INTERVAL_SECONDS` | `60` | No | Interval in seconds between scheduler heartbeat no-ops |
| `JOB_COALESCE` | `false` | No | Merge multiple queued misfired runs into one |
| `JOB_MAX_INSTANCES` | `1` | No | Max concurrent instances of the same job |
| `JOB_MISFIRE_GRACE_TIME` | `60` | No | Seconds after scheduled time a job is still allowed to run |
| `USER_ID_PATH_LIST` | `["sub","userClaims.sub.0","project"]` | No | Ordered JSON paths used to extract the caller's user ID from DIAL claims |
| `SCHEDULER_APP_ID` | `dial_scheduler` | No | This app's DIAL application id, used to build the on-behalf-of credentials request (`applications/{SCHEDULER_APP_ID}/external_services/{service_id}`) |
| `WORKER_HEALTH_DIR` | `/tmp` | No | Directory holding the `started`/`heartbeat`/`ready` marker files used by Kubernetes `exec` probes |
| `WORKER_LIVENESS_STALENESS_SECONDS` | `180.0` | No | Max age of the `heartbeat` marker before the worker is considered hung |
| `WORKER_READINESS_CHECK_INTERVAL_SECONDS` | `15.0` | No | How often the worker polls database connectivity to maintain the `ready` marker |
| **Database** | | | |
| `DB_HOST` | `localhost` | No | PostgreSQL host |
| `DB_PORT` | `5432` | No | PostgreSQL port |
| `DB_NAME` | `app` | No | Database name |
| `DB_USER` | `app` | Yes | Database username |
| `DB_AUTH_MODE` | `password` | No | Auth mode: `password` or `connection-string` |
| `DB_PASSWORD` | — | Yes | Database password |
| `DB_URL` | — | Yes | Full SQLAlchemy connection string |
| `DB_DRIVER` | `postgresql+asyncpg` | No | SQLAlchemy driver dialect |
| `DB_POOL_SIZE` | `10` | No | Connection pool size |
| `DB_MAX_OVERFLOW` | `20` | No | Max overflow connections above pool size |
| `DB_POOL_RECYCLE_SECONDS` | `1800` | No | Recycle connections older than this many seconds |
| `DB_POOL_TIMEOUT_SECONDS` | `30` | No | Timeout waiting to acquire a connection from the pool |
| `DB_POOL_PRE_PING` | `true` | No | Test connections with a ping before use |
| `DB_ECHO` | `false` | No | Log all SQL statements |
| **HTTP Client** | | | |
| `WORKER_HTTP_TIMEOUT_SECONDS` | `60.0` | No | Overall outbound request timeout |
| `WORKER_HTTP_CONNECT_TIMEOUT_SECONDS` | `5.0` | No | TCP connection timeout |
| `WORKER_HTTP_READ_TIMEOUT_SECONDS` | `60.0` | No | Socket read timeout |
| `WORKER_HTTP_WRITE_TIMEOUT_SECONDS` | `60.0` | No | Socket write timeout |
| `WORKER_HTTP_POOL_TIMEOUT_SECONDS` | `5.0` | No | Timeout waiting for a pooled connection |
| `WORKER_HTTP_MAX_CONNECTIONS` | `200` | No | Max total HTTP connections |
| `WORKER_HTTP_MAX_KEEPALIVE_CONNECTIONS` | `20` | No | Max keep-alive connections |

## Quality Gates

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml
```

All commands must exit `0` before merging.

## Teardown

```bash
docker compose down -v   # stops containers and removes volumes (wipes the DB)
```

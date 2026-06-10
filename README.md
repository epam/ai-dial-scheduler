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

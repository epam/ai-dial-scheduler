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

## Quality Gates

Install dev dependencies first:

```bash
uv sync
```

Then run all gates:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src --cov-report=term-missing
```

All commands must exit `0` before merging.

## Teardown

```bash
docker compose down -v   # stops containers and removes volumes (wipes the DB)
```

# Quickstart: Scheduler Application Skeleton

**Feature**: `001-scheduler-app-skeleton` | **Date**: 2026-06-09

This guide validates all acceptance scenarios from the spec end-to-end.

---

## Prerequisites

- Docker (≥ 24) and Docker Compose v2 installed
- `uv` installed (for quality gates run on the host)
- Port `5432` and `5000` available on the host

---

## 1. Start the Full Stack

```bash
cp .env.example .env      # fill in placeholder values (see .env.example comments)
docker compose up --build
```

Expected startup sequence (all within ~2 minutes):
1. `postgres` starts and passes its health check
2. `migrations` container runs `alembic upgrade head` and exits `0`
3. `api` and `worker` containers start

**Acceptance scenario 1 verified** when all three components appear as running with no exit errors.

---

## 2. Validate the API Health Endpoint

```bash
curl -s http://localhost:5000/health
```

Expected response:

```json
{"status": "ok"}
```

HTTP status: `200 OK`

For full contract details see [`contracts/health.md`](contracts/health.md).

**Acceptance scenario 2 verified** when this response is received within 5 seconds of the API becoming ready.

---

## 3. Confirm Worker Is Alive

```bash
docker compose logs worker
```

Expected output includes a structured log line like:
```json
{"timestamp": "...", "level": "info", "event": "Starting worker.", ...}
```

The worker process must be in `running` state (not `exited`):
```bash
docker compose ps worker
```

**Acceptance scenario 3 verified** when the worker status is `running` and the start log is present.

---

## 4. Quality Gates

All commands below must be run in the project root (where `pyproject.toml` lives). Install dev dependencies first:

```bash
uv sync
```

### 4a. Code Style & Formatting

```bash
uv run ruff check src tests
uv run ruff format --check src tests
```

Expected: no output (zero violations, zero formatting diffs).

**Acceptance scenario 4 verified** when both commands exit `0`.

### 4b. Type Analysis

```bash
uv run mypy src
```

Expected: `Success: no issues found in N source files`.

**Acceptance scenario 5 verified** when exit code is `0` with no errors.

### 4c. Unit Tests with Coverage

```bash
uv run pytest --cov=src --cov-report=term-missing
```

Expected: all tests pass; a coverage report is printed to stdout; `coverage.xml` is written.

**Acceptance scenario 6 verified** when exit code is `0` and coverage output is shown.

---

## 5. First-Time Database Initialisation

On fresh `docker compose up`, the migrations container runs:
```
alembic upgrade head
```
APScheduler's `SQLAlchemyJobStore` creates its own `apscheduler_jobs` table when the worker starts (not via Alembic).

**Acceptance scenario 8 verified** when:
- The migrations container exits with code `0`
- No manual migration commands were run by the developer

---

## 6. Edge Case: DB Unavailable at API/Worker Start

The `docker-compose.yml` uses `depends_on` with `condition: service_healthy` for postgres and `condition: service_completed_successfully` for the migrations service. This means the API and worker do not start until:
1. Postgres is accepting connections
2. Migrations have completed

If postgres is stopped after startup, the API will return `500` on database-dependent endpoints (none exist in this skeleton). The worker will log an error on the next scheduler tick attempting to reach the jobstore.

---

## Teardown

```bash
docker compose down -v    # stops all containers and removes volumes (wipes the DB)
```

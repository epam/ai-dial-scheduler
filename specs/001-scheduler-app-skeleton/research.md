# Research: Scheduler Application Skeleton

**Feature**: `001-scheduler-app-skeleton` | **Date**: 2026-06-09

All package versions verified against PyPI on 2026-06-09.

---

## 1. Package Versions

### Decision
Pin all dependencies to latest stable major versions. Versions confirmed via `https://pypi.org/pypi/{package}/json`.

| Package | Version | Notes |
|---------|---------|-------|
| apscheduler | 3.11.2 | Latest 3.x stable; 4.x is still alpha — do not use |
| aidial-sdk | 0.37.0 | DIALApp, healthcheck, telemetry |
| sqlalchemy | 2.0.50 | Async engine via asyncpg |
| sqlmodel | 0.0.38 | SQLModel ORM wrapping SQLAlchemy |
| alembic | 1.18.4 | Migrations |
| asyncpg | 0.31.0 | Async PostgreSQL driver for app queries |
| psycopg2-binary | 2.9.12 | Sync driver for APScheduler SQLAlchemyJobStore |
| pydantic-settings | 2.14.1 | Environment-based settings |
| structlog | 26.1.0 | Structured JSON logging |
| httpx | 0.28.1 | Async HTTP client in app state |
| uvicorn | 0.49.0 | ASGI server |
| ruff | 0.15.16 | Lint + format |
| mypy | 2.1.0 | Type checking |
| pytest | 9.0.3 | Test runner |
| pytest-asyncio | 1.4.0 | Async test support |
| pytest-cov | 7.1.0 | Coverage |
| pytest-mock | 3.15.1 | Mocking |
| pytest-sugar | 1.1.1 | Output formatting |
| pre-commit | 4.6.0 | Git hooks |

**Rationale**: Latest stable within each major version line. APScheduler 3.x is stable and production-ready; APScheduler 4.x (alpha) has a completely different API and would require a full rewrite later — this aligns with the user's explicit constraint.

**Alternatives considered**: APScheduler 4.x (alpha) — rejected because the user explicitly requires 3.x and 4.x is not production-ready as of 2026-06-09.

---

## 2. APScheduler 3.x — SQLAlchemyJobStore Setup

### Decision
Use `SQLAlchemyJobStore(url=psycopg2_sync_url)` with the URL returned by `build_database_url(settings, driver=DbDriver.SYNC)`.

**Correct import path** (verified from APScheduler 3.x source):
```python
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
```

**Constructor**:
```python
SQLAlchemyJobStore(url="postgresql+psycopg2://user:pass@host:5432/db")
```
- The `url` kwarg accepts any SQLAlchemy-compatible sync connection string.
- APScheduler creates/manages its own table (`apscheduler_jobs`) — it is NOT managed by Alembic.
- `tablename` kwarg can override the default table name if needed.

**Scheduler configuration**:
```python
scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=sync_url)},
    job_defaults={"coalesce": False, "max_instances": 1},
    timezone="UTC",
)
```

**Rationale**: `AsyncIOScheduler` schedules jobs on the running asyncio event loop, which means async job coroutines run correctly. `SQLAlchemyJobStore` with psycopg2 blocks the event loop briefly on each scheduler tick (one DB query every few seconds to check for due jobs), but this is acceptable for infrequent cron-style firing.

**Alternatives considered**: `BackgroundScheduler` with threads — rejected because it does not run async coroutines natively.

---

## 3. Worker Keep-Alive & Graceful Shutdown

### Decision
Use `asyncio.Event` as the process liveness gate; register `SIGTERM`/`SIGINT` signal handlers on the running loop; call `scheduler.shutdown(wait=True)` in the `finally` block.

**Pattern**:
```python
import asyncio
import signal

async def _run() -> None:
    scheduler = create_worker()
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("Starting worker.")
    scheduler.start()
    try:
        await stop_event.wait()   # blocks until signal received
    finally:
        scheduler.shutdown(wait=True)
        logger.info("Worker stopped.")

def run_worker() -> None:
    asyncio.run(_run())
```

**Why `asyncio.Event` over `asyncio.sleep(float("inf"))`**: The event approach responds immediately to signals without a polling delay. `sleep(float("inf"))` also works but is less idiomatic and harder to test.

**Rationale**: `scheduler.start()` in APScheduler 3.x asyncio mode is non-blocking — it schedules periodic ticks on the event loop but does not block. Without a keep-alive primitive, `asyncio.run(_run())` exits immediately after start, terminating the worker process.

**Docker graceful shutdown**: Kubernetes/Docker sends `SIGTERM` before `SIGKILL`. With this pattern, the worker has up to `terminationGracePeriodSeconds` (default 30s) to finish any in-progress jobs before the container is killed.

**Alternatives considered**: `loop.run_forever()` — does not compose with `asyncio.run()` and is deprecated in recent Python. Using `threading.Event` — blocked the asyncio loop when awaited.

---

## 4. aidial-sdk DIALApp API

### Decision
Use `DIALApp(dial_url=..., title=..., add_healthcheck=True, telemetry_config=TelemetryConfig(...), lifespan=lifespan, redirect_slashes=False)`.

**Verified from source (`aidial_sdk/application.py` @ `development` branch)**:

- `DIALApp` is a `FastAPI` subclass accepting all FastAPI constructor kwargs via `**kwargs`.
- `add_healthcheck=True` registers `GET /health` returning `{"status": "ok"}` as a `JSONResponse`.
- Health endpoint logs are filtered from uvicorn access logs automatically.
- `telemetry_config: TelemetryConfig | None` — pass `TelemetryConfig(service_name=settings.app_name)` to enable OTLP tracing.
- `dial_url` is optional; required only if header propagation is enabled.
- `lifespan` is passed through to `FastAPI.__init__` via `**kwargs`.

**For the worker (non-FastAPI) service**, use `init_telemetry` directly:
```python
from aidial_sdk.telemetry.init import init_telemetry
from aidial_sdk.telemetry.types import TelemetryConfig

init_telemetry(app=None, config=TelemetryConfig(service_name=settings.app_name))
```
The `app=None` path skips FastAPI instrumentation but still sets up OTLP trace/metric exporters.

**Health endpoint response**:
```json
{"status": "ok"}
```
HTTP 200.

**Rationale**: aidial-sdk provides DIAL-platform-compatible telemetry, CORS, and health check with a single constructor parameter, avoiding manual setup.

---

## 5. aidial-sdk Logging Conflict

### Finding
`aidial_sdk/application.py` calls `logging.config.dictConfig(LogConfig().model_dump())` at **module import time** (top-level statement, not inside a function). This means importing `DIALApp` configures Python's logging before our `configure_logging()` runs.

### Decision
Place `configure_logging()` **after all imports** in `src/api/app.py`. Since Python executes module-level statements in order, our `configure_logging()` call will override aidial-sdk's logging config after the import completes.

```python
# src/api/app.py — correct order
from aidial_sdk import DIALApp           # ← triggers aidial-sdk logging config
# ... other imports ...
from src.lib.utils.logging import configure_logging, logger

configure_logging()   # ← overrides aidial-sdk config with structlog setup
```

**Important**: `configure_logging()` must NOT be called before imports, because `from aidial_sdk import DIALApp` would then immediately overwrite it. The patterns.md already shows this correct order (imports first, then `configure_logging()`). This is consistent with the constraint "called once at module level."

---

## 6. Docker Compose Health Checks & Service Ordering

### Decision
Use `depends_on` with `condition: service_healthy` for health-check-based startup ordering. Run migrations as an `init`-style one-shot service that the API and worker depend on.

**Pattern**:
```yaml
services:
  postgres:
    image: postgres:16-alpine
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER} -d ${DB_NAME}"]
      interval: 5s
      timeout: 3s
      retries: 10

  migrations:
    build: .
    environment:
      SERVICE: migrations
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"

  api:
    build: .
    environment:
      SERVICE: api
    depends_on:
      postgres:
        condition: service_healthy
      migrations:
        condition: service_completed_successfully

  worker:
    build: .
    environment:
      SERVICE: worker
    depends_on:
      postgres:
        condition: service_healthy
      migrations:
        condition: service_completed_successfully
```

**Rationale**: `service_completed_successfully` ensures migrations finish before API or worker starts — addressing the race condition edge case from the spec. The postgres `healthcheck` ensures the DB is actually accepting connections, not just that the container has started.

**Alternatives considered**: `restart: always` on migrations — creates a restart loop; rejected. `depends_on` without health checks — does not guarantee DB readiness; rejected.

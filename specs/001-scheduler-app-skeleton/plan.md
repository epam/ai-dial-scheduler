# Implementation Plan: Scheduler Application Skeleton

**Branch**: `001-scheduler-app-skeleton` | **Date**: 2026-06-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-scheduler-app-skeleton/spec.md`

## Summary

Build the foundational skeleton for the AIDIAL Scheduler: a three-service application (api, worker, migrations) run from a single Docker image dispatched by the `SERVICE` environment variable. The API exposes only a health check via aidial-sdk (`add_healthcheck=True`); the worker runs an APScheduler 3.x `AsyncIOScheduler` with a `SQLAlchemyJobStore` backed by PostgreSQL (psycopg2 sync URL). A `docker compose` configuration starts all components with one command. All quality gates (ruff, mypy, pytest) pass; a README documents the system for new developers.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**:
- `aidial-sdk` — DIALApp FastAPI wrapper with built-in `/health` endpoint, OTLP telemetry, CORS
- `apscheduler~=3.11` — `AsyncIOScheduler` + `SQLAlchemyJobStore` (psycopg2 backend); 4.x is alpha, do not use
- `sqlalchemy` 2.x, `sqlmodel` — async ORM (asyncpg driver for app queries)
- `alembic` — schema migrations for app models (`BaseSQLModel` subclasses only)
- `asyncpg` — async PostgreSQL driver for app/API queries
- `psycopg2-binary` — sync driver required by APScheduler `SQLAlchemyJobStore`
- `pydantic-settings` v2 — environment-variable-based config
- `structlog` — structured JSON logging
- `httpx` — async HTTP client held in FastAPI app state
- `uvicorn` — ASGI server for the API process

**Dev Dependencies**: `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-mock`, `pytest-sugar`, `pre-commit`

**Storage**: PostgreSQL 16
- App schema → Alembic-managed (`BaseSQLModel` subclasses)
- APScheduler jobstore → APScheduler-managed (creates its own tables on scheduler start)
- Two separate URL pools: asyncpg (async engine) for app queries; psycopg2 (sync URL) for APScheduler

**Testing**: pytest with `asyncio_mode = "auto"`, `pytest-mock` for mocking, `pytest-cov` for coverage

**Target Platform**: Linux container (`python:3.14-alpine`); local dev via docker compose

**Project Type**: Backend web service (API) + background worker from a single codebase/image

**Performance Goals**: Skeleton only — correctness of async I/O and non-blocking event loop; no throughput SLAs

**Constraints**:
- APScheduler 3.x only (4.x is alpha)
- `SQLAlchemyJobStore` must receive a sync psycopg2 URL via `build_database_url(settings, driver=DbDriver.SYNC)` — never the async engine
- `configure_logging()` called once at module level in both `src/api/app.py` and `src/worker/app.py` before any other code
- No API job-scheduling routes in this skeleton (API exposes health check only)
- All settings access via `get_settings()` (lru_cached); no module-level settings reads (see patterns.md issues below)

**Scale/Scope**: Skeleton — establishes structural foundation; no business logic or scheduling routes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Pre-Design | Post-Design | Notes |
|-----------|-----------|-------------|-------|
| I. Reliability First | ✅ | ✅ | Worker startup/shutdown with SIGTERM handling; DB pool configured; async engine never shared with APScheduler |
| II. Observability (NON-NEGOTIABLE) | ✅ | ✅ | structlog via `configure_logging()`; all calls use `src.lib.utils.logging.logger`; OTLP via aidial-sdk for API; `init_telemetry` for worker |
| III. Test-First (NON-NEGOTIABLE) | ✅ | ✅ | TDD enforced in implement phase; unit tests for settings, DB URL builder, logging config smoke |
| IV. Contract-Driven API | ✅ | ✅ | Health endpoint contract documented in `contracts/`; no custom routes needed for skeleton |
| V. Simplicity & YAGNI | ✅ | ✅ | Skeleton only; no premature abstractions; `Database` singleton justified (see Complexity Tracking) |

No gate violations.

## Patterns.md Issues Flagged

The following defects in `specs/001-scheduler-app-skeleton/patterns.md` must be corrected during implementation:

### ISSUE-1: Module-level `get_settings()` call in `src/lib/utils/logging.py`

**Offending line**: `logger = structlog.get_logger(get_settings().app_name)`

**Problem**: `get_settings()` (and therefore full pydantic-settings environment parsing) is invoked when the module is first imported — before the entrypoint has set any environment. If the module is imported in test or cold-start context without env vars present, the lru_cache stores a misconfigured `Settings` object for the lifetime of the process.

**Fix**: Drop the app_name argument: `logger = structlog.get_logger()`. The app name is not required by structlog; the `add_logger_name` processor derives it from the call site. The structlog `BoundLogger` is lazy and does not evaluate processors at construction time.

### ISSUE-2: Module-level `get_settings()` in `migrations/env.py`

**Offending line**: `config.set_main_option("sqlalchemy.url", build_database_url(get_settings(), hide_password=True))`

**Problem**: Called at module scope. Alembic `env.py` runs as a standalone CLI tool where environment variables are always present before execution, so this is operationally safe — but it is architecturally inconsistent.

**Resolution**: Keep as-is with a comment documenting the exception. Alembic `env.py` is not imported by application code; it is invoked only by the Alembic CLI after env vars are exported.

### ISSUE-3: `_run()` in `src/worker/app.py` exits immediately

**Problem**: `scheduler.start()` is non-blocking in APScheduler 3.x asyncio mode. After it returns, `_run()` has no remaining awaitable, so `asyncio.run(_run())` exits and the worker process terminates.

**Fix**: After `scheduler.start()`, wait on a `asyncio.Event` that is only set by a signal handler. Add `SIGTERM`/`SIGINT` handlers that set the event and then call `scheduler.shutdown(wait=True)`. Pattern:

```python
async def _run() -> None:
    scheduler = create_worker()
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("Starting worker.")
    scheduler.start()
    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=True)
        logger.info("Worker stopped.")
```

### ISSUE-4: `lifespan` missing return type annotation

**Offending line**: `async def lifespan(app: DIALApp):`

**Problem**: mypy requires a return type on all annotated functions. `@asynccontextmanager` wraps an `AsyncGenerator`.

**Fix**:
```python
from collections.abc import AsyncGenerator

@asynccontextmanager
async def lifespan(app: DIALApp) -> AsyncGenerator[None, None]:
```

## Project Structure

### Documentation (this feature)

```text
specs/001-scheduler-app-skeleton/
├── plan.md              # This file
├── patterns.md          # User-defined patterns
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── health.md        # Health endpoint contract
└── tasks.md             # /speckit-tasks output (not created here)
```

### Source Code

```text
src/
├── __init__.py
├── api/
│   ├── __init__.py
│   ├── app.py                        # DIALApp factory, lifespan, run_api()
│   └── dependencies/
│       ├── __init__.py
│       └── db.py                     # get_db_session() FastAPI dependency
├── worker/
│   ├── __init__.py
│   └── app.py                        # AsyncIOScheduler factory, signal handling, run_worker()
└── lib/
    ├── __init__.py
    ├── settings.py                   # Settings, DatabaseSettings, get_settings()
    ├── constants.py                  # App-level constants (SERVICE names, etc.)
    ├── db/
    │   ├── __init__.py
    │   ├── engine.py                 # Database singleton, async engine/sessionmaker
    │   ├── enums.py                  # DbDriver, DbAuthMode
    │   ├── session.py                # get_session() async generator
    │   └── url.py                    # build_database_url()
    ├── models/
    │   ├── __init__.py               # re-exports BaseSQLModel
    │   └── base.py                   # BaseSQLModel, metadata with naming convention
    ├── schemas/
    │   └── __init__.py
    ├── repositories/
    │   └── __init__.py
    ├── services/
    │   └── __init__.py
    ├── exceptions/
    │   └── __init__.py
    └── utils/
        ├── __init__.py
        └── logging.py                # LoggingConfig, configure_logging(), logger

tests/
├── __init__.py
├── unit/
│   ├── __init__.py
│   ├── lib/
│   │   ├── __init__.py
│   │   ├── test_settings.py          # Settings validation edge cases
│   │   ├── test_db_url.py            # build_database_url() all driver/auth combinations
│   │   └── test_logging.py           # configure_logging() smoke, logger is callable
│   └── worker/
│       ├── __init__.py
│       └── test_worker_app.py        # create_worker() scheduler is configured correctly
└── integration/
    └── __init__.py                   # Placeholder — integration tests in future features

migrations/
├── env.py                            # Alembic env (async runner + module-level URL exception)
├── script.py.mako                    # Migration template
└── versions/                         # Empty for skeleton — no app models yet

entrypoint_api.py                     # from src.api.app import run_api; run_api()
entrypoint_worker.py                  # from src.worker.app import run_worker; run_worker()
entrypoint.sh                         # Dispatches SERVICE: migrations | api | worker
Dockerfile                            # python:3.14-alpine, single image, three SERVICE modes
docker-compose.yml                    # postgres + migrations (init) + api + worker
.env.example                          # All required env vars with placeholder values
alembic.ini                           # Alembic config pointing at migrations/
pyproject.toml                        # Project metadata + ruff/mypy/pytest config
.pre-commit-config.yaml               # check-yaml, end-of-file-fixer, ruff, mypy hooks
README.md                             # Run instructions, component descriptions, quality gates
```

**Structure Decision**: Follows constitution v1.2.0 exactly — `api/` and `worker/` are service entry points; `lib/` is the shared domain. No imports from `src.api` or `src.worker` inside `lib/`. Circular imports are a CI gate blocker.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| `Database` singleton class | Async engine must be shared across the FastAPI app without using a module-level global (forbidden by constitution). A singleton scoped to the app lifespan without app state access is needed by Alembic `env.py` too. | A module-level `engine` variable is a forbidden global. Storing the engine only in `app.state` would make it inaccessible from `migrations/env.py` and the worker. |
| Two separate DB connections (asyncpg + psycopg2) | APScheduler 3.x `SQLAlchemyJobStore` is synchronous and requires a psycopg2 URL; it cannot share the async engine. | Using psycopg2 for everything would block the event loop on all app queries, violating Reliability (Principle I). |

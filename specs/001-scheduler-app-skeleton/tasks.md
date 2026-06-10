---
description: "Task list for Scheduler Application Skeleton"
---

# Tasks: Scheduler Application Skeleton

**Input**: Design documents from `specs/001-scheduler-app-skeleton/`

**Prerequisites**: plan.md ✅ · spec.md ✅ · research.md ✅ · data-model.md ✅ · contracts/health.md ✅ · quickstart.md ✅ · patterns.md ✅

**Tests**: Included — constitution Principle III (Test-First) is NON-NEGOTIABLE; unit tests are written before implementation (TDD Red-Green-Refactor).

**Organization**: Single user story — all implementation serves US1.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[US1]**: Belongs to User Story 1 (the only story in this feature)
- Exact file paths included in all implementation tasks

## Path Conventions

- Source root: `src/`
- Tests root: `tests/`
- Migrations: `migrations/`
- Config files at repository root

---

## Phase 1: Setup (Project Initialization)

**Purpose**: Project skeleton, tooling configuration, and package structure

- [X] T001 Create all Python package `__init__.py` placeholder files: `src/__init__.py`, `src/api/__init__.py`, `src/api/dependencies/__init__.py`, `src/worker/__init__.py`, `src/lib/__init__.py`, `src/lib/db/__init__.py`, `src/lib/models/__init__.py`, `src/lib/schemas/__init__.py`, `src/lib/repositories/__init__.py`, `src/lib/services/__init__.py`, `src/lib/exceptions/__init__.py`, `src/lib/utils/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/lib/__init__.py`, `tests/unit/worker/__init__.py`, `tests/integration/__init__.py`; also create empty `migrations/versions/` directory
- [X] T00X Create `pyproject.toml` — name `aidial-scheduler`, `requires-python = ">=3.14,<3.15"`, runtime deps pinned from research.md (aidial-sdk==0.37.0, apscheduler~=3.11, sqlalchemy==2.0.50, sqlmodel==0.0.38, alembic==1.18.4, asyncpg==0.31.0, psycopg2-binary==2.9.12, pydantic-settings==2.14.1, structlog==26.1.0, httpx==0.28.1, uvicorn==0.49.0); dev group (ruff==0.15.16, mypy==2.1.0, pytest==9.0.3, pytest-asyncio==1.4.0, pytest-cov==7.1.0, pytest-mock==3.15.1, pytest-sugar==1.1.1, pre-commit==4.6.0) with `<next-major` upper bounds; ruff config (line-length=120, src=["src","tests"], target-version=py314, select=["E","F","I"], isort with known-first-party=["src"]); mypy (python_version=3.14, pydantic.mypy plugin, ignore_missing_imports=true); pytest (pythonpath=["."], asyncio_mode=auto); `[tool.uv] package = false`; run `uv lock` after creation to generate `uv.lock`
- [X] T00X [P] Create `.pre-commit-config.yaml` — three hook repos: pre-commit-hooks (check-yaml, end-of-file-fixer, trailing-whitespace), ruff-pre-commit (ruff-check with args `["--fix","--no-unsafe-fixes"]` + ruff-format), mirrors-mypy (mypy with `additional_dependencies: [pydantic>=2, fastapi]`); substitute latest stable `rev:` tag for each repo per patterns.md
- [X] T00X [P] Create `alembic.ini` — `script_location = migrations`, `file_template = %%(year)d%%(month).2d%%(day).2d_%%(hour).2d%%(minute).2d_%%(rev)s_%%(slug)s`, `sqlalchemy.url` left as placeholder (overridden programmatically in `migrations/env.py`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared `lib/` package — both the API and worker depend on all of these; no user story work can begin until this phase completes

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T00X [P] Create `src/lib/db/enums.py` — `DbDriver(str, Enum)` with `ASYNC = "postgresql+asyncpg"` and `SYNC = "postgresql+psycopg2"`; `DbAuthMode(str, Enum)` with `PASSWORD = "password"` and `CONNECTION_STRING = "connection-string"` per patterns.md
- [X] T00X [P] Create `src/lib/constants.py` — string constants `SERVICE_API = "api"`, `SERVICE_WORKER = "worker"`, `SERVICE_MIGRATIONS = "migrations"` used by the entrypoint dispatcher
- [X] T00X [P] Create `src/lib/models/base.py` — SQLAlchemy `MetaData` with naming convention (`IX_%(column_0_label)s`, `UQ_%(table_name)s_%(column_0_name)s`, `CK_%(table_name)s_%(constraint_name)s`, `FK_%(table_name)s_%(column_0_name)s_%(referred_table_name)s`, `PK_%(table_name)s`); assign `SQLModel.metadata = metadata`; `BaseSQLModel(SQLModel)` with `__abstract__ = True`, `@declared_attr.directive __tablename__` (CamelCase→snake_case + "s"), `id: int` PK, `created_at` and `updated_at` timezone-aware `DateTime` with `server_default=func.now()` per patterns.md (no local lib imports — fully parallel)
- [X] T00X Create `src/lib/settings.py` — `ENV_FILE_MODEL_CONFIG` using `SettingsConfigDict(env_file=".env", extra="ignore")`; `DatabaseSettings(BaseSettings)` (db_driver, db_host, db_port, db_name, db_user, db_pool_size, db_max_overflow, db_pool_recycle_seconds, db_pool_pre_ping, db_echo, db_auth_mode, db_password: str|None, db_url: str|None + `@model_validator(mode="after")` enforcing required fields per auth mode); `Settings(BaseSettings)` (env, app_name, dial_url, dial_api_key: SecretStr, port=5000, log_level, db: DatabaseSettings = DatabaseSettings()); `@lru_cache get_settings() -> Settings` per patterns.md (depends on T005)
- [X] T00X [P] Create `src/lib/db/url.py` — `build_database_url(settings: Settings, *, driver: DbDriver | None = None, hide_password: bool = False) -> str`: returns `settings.db.db_url` directly when `db_auth_mode == CONNECTION_STRING`; otherwise builds via `URL.create(drivername=(driver or settings.db.db_driver).value, ...)` and renders with `hide_password` per patterns.md (depends on T005, T008)
- [X] T01X [P] Create `src/lib/utils/logging.py` — `_order_keys` structlog processor (orders timestamp/level/event/logger first); `LoggingConfig` with `@staticmethod get_config() -> dict` (reads settings lazily via `get_settings()` inside the method, configures structured JSON using `ProcessorFormatter` with OTEL log level suppression); `configure_logging() -> None` (calls `dictConfig` + `structlog.configure` with `make_filtering_bound_logger`); **ISSUE-1 fix from plan.md**: `logger = structlog.get_logger()` — no `get_settings().app_name` argument; `structlog.get_logger()` is lazy and evaluates processors at bind time, not construction time (depends on T008)
- [X] T01X Create `src/lib/db/engine.py` — `Database` singleton using `__new__`/`__init__` guard (`_instance`, `_initialized`); `init_engine(*, poolclass=None, force_fresh=False) -> AsyncEngine`; `_init_engine_url_based(*, poolclass=None) -> AsyncEngine` calling `build_database_url(settings)` and `create_async_engine` with pool_size/max_overflow/pool_recycle/pool_pre_ping from settings; `engine` and `sessionmaker` properties with lazy init; `async dispose() -> None` per patterns.md (depends on T005, T008, T009)
- [X] T01X Create `src/lib/db/session.py` — `get_session() -> AsyncGenerator[AsyncSession, None]` using `Database().sessionmaker()` as async context manager per patterns.md (depends on T011)
- [X] T01X Create `src/lib/models/__init__.py` — `from .base import BaseSQLModel` and `__all__ = ["BaseSQLModel"]` (depends on T007)

**Checkpoint**: Shared library complete — API and worker implementation can now begin in parallel

---

## Phase 3: User Story 1 — Developer Establishes and Validates the Scheduler Foundation (Priority: P1) 🎯 MVP

**Goal**: A developer clones the repo, runs `docker compose up --build`, verifies the API health endpoint and worker liveness, runs all quality gates, and understands the system from the README alone.

**Independent Test**: `docker compose up --build` succeeds within 2 minutes; `curl http://localhost:5000/health` returns `{"status":"ok"}` HTTP 200; `docker compose ps worker` shows `running` state; `uv run ruff check src tests`, `uv run mypy src`, and `uv run pytest --cov=src` all exit 0.

### Tests for User Story 1 (TDD — write FIRST, verify RED before implementing) ⚠️

> **Constitution Principle III (NON-NEGOTIABLE): Write all four test files before writing any implementation. Run `uv run pytest` and confirm each file fails (ImportError or assertion failure) before proceeding to implementation.**

- [X] T01X [P] [US1] Write `tests/unit/lib/test_settings.py` — cover: `DatabaseSettings` with `PASSWORD` mode raises `ValueError` when `db_password` is None; `DatabaseSettings` with `CONNECTION_STRING` mode raises `ValueError` when `db_url` is None; valid instances for both modes succeed; `Settings` populates all fields from monkeypatched env vars; `get_settings()` returns the same cached instance on repeated calls; use `monkeypatch.setenv` and `monkeypatch.delenv` to isolate env state; clear `get_settings.cache_info` between tests where needed
- [X] T01X [P] [US1] Write `tests/unit/lib/test_db_url.py` — cover: `build_database_url` with PASSWORD+ASYNC returns URL starting with `postgresql+asyncpg://`; `build_database_url` with `driver=DbDriver.SYNC` returns URL starting with `postgresql+psycopg2://` regardless of `settings.db.db_driver`; CONNECTION_STRING mode returns `settings.db.db_url` verbatim; `hide_password=True` omits the password value in the returned string; use a fixture that builds a minimal valid `Settings` object via `monkeypatch`
- [X] T01X [P] [US1] Write `tests/unit/lib/test_logging.py` — cover: `configure_logging()` completes without raising any exception; `LoggingConfig.get_config()` returns a dict containing keys `version`, `formatters`, `handlers`, `root`; `logger` imported from `src.lib.utils.logging` is not None and has callable `.info`, `.warning`, `.error` attributes; all assertions import from `src.lib.utils.logging`
- [X] T01X [P] [US1] Write `tests/unit/worker/test_worker_app.py` — cover: `create_worker()` returns an `AsyncIOScheduler` instance; `scheduler.get_jobstore("default")` is a `SQLAlchemyJobStore`; `str(scheduler.timezone)` equals `"UTC"`; `scheduler._job_defaults` contains `coalesce=False` and `max_instances=1`; use `mocker.patch("src.worker.app.get_settings")` returning a valid settings fixture to avoid real DB connections

### Implementation for User Story 1

- [X] T01X [P] [US1] Implement `src/api/app.py` — all imports first (DIALApp, TelemetryConfig, AsyncGenerator, httpx, CORSMiddleware, uvicorn, Database, get_settings, LoggingConfig, configure_logging, logger); then `configure_logging()` at module level **after** all imports (research §5: must override aidial-sdk's module-level dictConfig which runs on DIALApp import); `@asynccontextmanager async def lifespan(app: DIALApp) -> AsyncGenerator[None, None]` (**ISSUE-4 fix from plan.md**: explicit AsyncGenerator return type); `create_api() -> DIALApp` with `add_healthcheck=True`, `TelemetryConfig(service_name=settings.app_name)`, CORSMiddleware; `run_api() -> None` with uvicorn factory mode (`"src.api.app:create_api"`) per patterns.md
- [X] T01X [P] [US1] Implement `src/api/dependencies/db.py` — `get_db_session() -> AsyncGenerator[AsyncSession, None]` delegating to `get_session()` for use as FastAPI `Depends` injection per patterns.md (depends on T012)
- [X] T02X [P] [US1] Implement `src/worker/app.py` — imports first, then `configure_logging()` at module level; `create_worker() -> AsyncIOScheduler` with `SQLAlchemyJobStore(url=build_database_url(settings, driver=DbDriver.SYNC))` as `"default"` jobstore, `job_defaults={"coalesce": False, "max_instances": 1}`, `timezone="UTC"`; `async _run() -> None` with `stop_event = asyncio.Event()`, `loop.add_signal_handler(signal.SIGTERM, stop_event.set)` and `loop.add_signal_handler(signal.SIGINT, stop_event.set)`, `scheduler.start()`, `await stop_event.wait()`, `finally: scheduler.shutdown(wait=True); logger.info("Worker stopped.")` (**ISSUE-3 fix from plan.md**: `scheduler.start()` is non-blocking in APScheduler 3.x; without the event the process exits immediately); `run_worker() -> None` calling `asyncio.run(_run())` per research §2 and §3
- [X] T02X [P] [US1] Create `migrations/env.py` — async Alembic runner: `target_metadata = BaseSQLModel.metadata`; module-level `config.set_main_option("sqlalchemy.url", build_database_url(get_settings(), hide_password=True))` with comment: `# plan.md ISSUE-2: module-level get_settings() is intentional here — env.py is only invoked by the Alembic CLI after env vars are exported, never imported by application code`; `run_migrations_online()` using `Database().init_engine(poolclass=pool.NullPool, force_fresh=True)` and `async with connectable.connect() as connection: await connection.run_sync(do_run_migrations)` followed by `await app_db.dispose()` per patterns.md
- [X] T02X [P] [US1] Create `migrations/script.py.mako` — migration template with `revision`, `down_revision`, `branch_labels`, `depends_on` variables; empty `upgrade() -> None` and `downgrade() -> None` stubs with `pass`; include `from alembic import op` and `import sqlalchemy as sa` imports per patterns.md
- [X] T02X [US1] Create `entrypoint.sh` — validates `$SERVICE` is set (exit 1 if empty); dispatches to `alembic upgrade head` (migrations), `python entrypoint_api.py` (api), `python entrypoint_worker.py` (worker), or exits 1 with "Unknown SERVICE" message; create `entrypoint_api.py` (`from src.api.app import run_api` + `if __name__ == "__main__": run_api()`) and `entrypoint_worker.py` (`from src.worker.app import run_worker` + `if __name__ == "__main__": run_worker()`) per patterns.md (depends on T018, T020)
- [X] T02X [US1] Create `Dockerfile` — `FROM --platform=linux/amd64 python:3.14-alpine`; `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy APP_HOME=/home/app HOME=/home/app`; `WORKDIR $APP_HOME`; `apk add` build deps (curl, alpine-sdk, linux-headers, unixodbc, postgresql-dev, gcc, python3-dev, musl-dev); `pip install uv==0.9.28`; `COPY pyproject.toml uv.lock ./` then `uv sync --frozen --no-dev`; copy src/, alembic.ini, migrations/, entrypoint files; create non-root user uid 1001 (`adduser -u 1001 --system --disabled-password --gecos "" app`); `ENV PATH="$APP_HOME/.venv/bin:$PATH"`; `USER app`; `CMD ["sh", "entrypoint.sh"]` per patterns.md (depends on T023)
- [X] T02X [US1] Create `docker-compose.yml` — `postgres:16-alpine` service with `pg_isready -U ${DB_USER} -d ${DB_NAME}` healthcheck (interval 5s, timeout 3s, retries 10); `migrations` one-shot service (`SERVICE: migrations`, `restart: "no"`, `depends_on: postgres: condition: service_healthy`); `api` and `worker` services (`SERVICE: api`/`worker`) with `depends_on: postgres: condition: service_healthy` AND `migrations: condition: service_completed_successfully`; api maps `"${PORT:-5000}:5000"`; all services load env from `.env` file; all services `build: .` per research §6 (depends on T024)
- [X] T02X [P] [US1] Create `.env.example` — placeholder values with inline comments for every required env var: `DIAL_URL=https://dial.example.com`, `DIAL_API_KEY=changeme`, `DB_HOST=postgres`, `DB_PORT=5432`, `DB_NAME=scheduler`, `DB_USER=scheduler`, `DB_PASSWORD=changeme`, `DB_AUTH_MODE=password`, `LOG_LEVEL=INFO`, `PORT=5000`, `ENV=dev`, `APP_NAME=aidial-scheduler`; add comment at top: `# Copy to .env and fill in real values before running docker compose up`
- [X] T02X [US1] Create `README.md` — sections: **Project Overview** (purpose of scheduler, two-service architecture: API + worker + shared DB); **Components** (API: health check, future scheduling routes; Worker: APScheduler background executor; Database: PostgreSQL jobstore); **Prerequisites** (Docker ≥24, Docker Compose v2, `uv` for quality gates); **Quickstart** (`cp .env.example .env && docker compose up --build`); **Verifying Health** (`curl -s http://localhost:5000/health` → `{"status":"ok"}`); **Verifying Worker** (`docker compose logs worker`, `docker compose ps worker`); **Quality Gates** (exact commands: `uv sync`, `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest --cov=src --cov-report=term-missing`); **Teardown** (`docker compose down -v`) per spec FR-010 and quickstart.md (depends on T025, T026)

**Checkpoint**: User Story 1 complete — `docker compose up --build` succeeds, API health responds, worker is alive, all quality gates pass

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Quality gate validation pass and fixes across all implemented files

- [X] T02X Run `uv run ruff check src tests` and `uv run ruff format src tests`; fix all reported linting violations (ruff format is auto-applied; check violations require manual correction); re-run until both commands exit 0
- [X] T02X Run `uv run mypy src` and fix all type errors; pay attention to: lifespan `-> AsyncGenerator[None, None]` (ISSUE-4), `Optional[AsyncEngine]` fields in `Database`, `async_sessionmaker` type parameter, `SecretStr` usage in settings, and `declared_attr.directive` type ignore comments
- [X] T03X Run `uv run pytest --cov=src --cov-report=term-missing` and fix all failing tests; verify Red→Green: tests T014–T017 must transition from failing (pre-implementation) to passing after T018–T022 complete; confirm coverage report is produced
- [X] T03X Validate quickstart.md scenarios end-to-end: `cp .env.example .env && docker compose up --build`; confirm `migrations` container exits 0; confirm `curl -s http://localhost:5000/health` returns `{"status":"ok"}`; confirm `docker compose ps worker` shows `running`; confirm `docker compose logs worker` contains `"Starting worker."` structured log line

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — **BLOCKS all user story work**
- **US1 Tests (T014–T017)**: Can begin after Phase 2 — write failing tests before implementation
- **US1 Implementation (T018–T027)**: Depends on Phase 2 complete + tests written (T014–T017)
- **Polish (Phase 4)**: Depends on all Phase 3 tasks complete

### Within Phase 2 (Foundational)

| Task | Depends On |
|------|-----------|
| T005 (enums) | Phase 1 only — fully parallel with T006, T007 |
| T006 (constants) | Phase 1 only — fully parallel with T005, T007 |
| T007 (models/base.py) | Phase 1 only — no local lib imports — fully parallel with T005, T006 |
| T008 (settings) | T005 (uses DbDriver, DbAuthMode) |
| T009 (db/url.py) | T005, T008 — parallel with T010 |
| T010 (logging) | T008 — parallel with T009 |
| T011 (db/engine.py) | T005, T008, T009 |
| T012 (db/session.py) | T011 |
| T013 (models/__init__.py) | T007 |

### Within Phase 3 (User Story 1)

| Task | Depends On |
|------|-----------|
| T014–T017 (tests) | Phase 2 — all parallel with each other |
| T018 (api/app.py) | Phase 2 (T010, T011) — parallel with T019–T022, T026 |
| T019 (api/dependencies/db.py) | T012 — parallel with T018, T020–T022, T026 |
| T020 (worker/app.py) | Phase 2 (T008, T009, T010) — parallel with T018, T019, T021, T022, T026 |
| T021 (migrations/env.py) | Phase 2 (T008, T011, T013) — parallel with T018–T020, T022, T026 |
| T022 (migrations/script.py.mako) | Phase 2 — parallel with T018–T021, T026 |
| T023 (entrypoints) | T018, T020 |
| T024 (Dockerfile) | T023 |
| T025 (docker-compose.yml) | T024 |
| T026 (.env.example) | Phase 2 — parallel with T018–T022 |
| T027 (README.md) | T025, T026 |

---

## Parallel Example: Phase 2 Foundational

```bash
# Batch 1 — fully independent, start together:
T005: "Create src/lib/db/enums.py"
T006: "Create src/lib/constants.py"
T007: "Create src/lib/models/base.py"

# Batch 2 — after T005:
T008: "Create src/lib/settings.py"

# Batch 3 — after T008, start together:
T009: "Create src/lib/db/url.py"
T010: "Create src/lib/utils/logging.py"

# Batch 4 — after T009:
T011: "Create src/lib/db/engine.py"

# Sequential after T011:
T012: "Create src/lib/db/session.py"

# After T007 (can run in Batch 1 or after):
T013: "Create src/lib/models/__init__.py"
```

## Parallel Example: Phase 3 User Story 1

```bash
# Batch 1 — write all failing tests together (TDD Red phase):
T014: "Write tests/unit/lib/test_settings.py"
T015: "Write tests/unit/lib/test_db_url.py"
T016: "Write tests/unit/lib/test_logging.py"
T017: "Write tests/unit/worker/test_worker_app.py"
# Run pytest — confirm ALL four test files fail before continuing

# Batch 2 — implementation after tests confirmed failing:
T018: "Implement src/api/app.py"
T019: "Implement src/api/dependencies/db.py"
T020: "Implement src/worker/app.py"
T021: "Create migrations/env.py"
T022: "Create migrations/script.py.mako"
T026: "Create .env.example"

# After T018 + T020:
T023: "Create entrypoint.sh + entrypoint_api.py + entrypoint_worker.py"

# After T023:
T024: "Create Dockerfile"

# After T024:
T025: "Create docker-compose.yml"

# After T025 + T026:
T027: "Create README.md"
```

---

## Implementation Strategy

### MVP First (This Feature IS the MVP)

1. Complete Phase 1 — project scaffolding and tooling
2. Complete Phase 2 — shared `lib/` package (critical path blocker)
3. Write tests T014–T017 — verify each fails (TDD Red)
4. Complete Phase 3 implementation T018–T027 — API, worker, containers, docs
5. **VALIDATE**: `docker compose up --build`; confirm API health, worker liveness, quality gates
6. Complete Phase 4 — fix any linting/type/test failures

### Incremental Delivery

This skeleton is a single deliverable. Each task completion advances toward the one acceptance criterion: all quality gates pass + `docker compose up --build` succeeds within 2 minutes.

### Key Implementation Constraints

| Constraint | Location | Source |
|-----------|----------|--------|
| APScheduler 3.x only; 4.x is alpha | T020 | plan.md, research.md |
| `SQLAlchemyJobStore` must receive psycopg2 sync URL via `build_database_url(settings, driver=DbDriver.SYNC)` — never the async engine | T020 | plan.md |
| `configure_logging()` must be called **after all imports** in api/app.py and worker/app.py | T018, T020 | research.md §5 |
| **ISSUE-1**: `logger = structlog.get_logger()` — no `get_settings().app_name` arg | T010 | plan.md ISSUE-1 |
| **ISSUE-3**: `_run()` must use `asyncio.Event` + signal handlers — `scheduler.start()` is non-blocking | T020 | plan.md ISSUE-3 |
| **ISSUE-4**: `lifespan` must declare `-> AsyncGenerator[None, None]` return type | T018 | plan.md ISSUE-4 |
| **ISSUE-2**: `migrations/env.py` module-level `get_settings()` is a documented exception — add comment | T021 | plan.md ISSUE-2 |
| All settings accessed via `get_settings()` (lru_cached); no module-level settings reads in application code | T008, all | plan.md |
| `migrations/versions/` starts empty — no `BaseSQLModel` subclasses in this skeleton | T001 | data-model.md |

---

## Notes

- `[P]` marks tasks that target different files with no incomplete-task dependencies — safe to parallelize
- `[US1]` maps all Phase 3 tasks to User Story 1 (only story in this feature)
- TDD cycle: write T014–T017 → run pytest and confirm failures → implement T018–T027 → run pytest and confirm all pass
- Phase 4 quality gate failures trace back to specific Phase 2/3 tasks — fix at source, not with workarounds
- The `uv.lock` file must be committed alongside `pyproject.toml` (generated by T002); the Dockerfile copies it and runs `uv sync --frozen --no-dev`
- `docker-compose.yml` `depends_on: migrations: condition: service_completed_successfully` is the key guard against the race condition edge case from the spec

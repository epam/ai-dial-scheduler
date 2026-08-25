# Code style and module organization

This document describes how the Dial Scheduler backend is structured and how to add or change
code in a consistent way. It is aimed at newcomers and contributors. It reflects the conventions
already established in the codebase; when in doubt, match the surrounding code.

The stack is **Python 3.14**, **FastAPI** (via `aidial-sdk`'s `DIALApp`), **APScheduler**,
**SQLAlchemy 2 / SQLModel** on PostgreSQL (async), **Alembic**, **Pydantic v2 /
pydantic-settings**, **structlog**, and **uv** as the package manager. Linting/formatting is
**ruff**; type checking is **mypy**.

---

## 1. Dependency injection (FastAPI `Depends`, not a DI container)

**We do not use a DI container (no `injector`).** Dependencies are wired two ways, and business
code never instantiates the things it depends on.

### Rules

- **API layer — FastAPI `Depends`.** Route handlers receive their collaborators through
  `Depends`. Providers live in `src/api/dependencies/` and assemble the object graph
  (session → repositories → service). Handlers never build a repository or a service directly.
- **Shared, long-lived components — app state.** Objects that must live for the process lifetime
  (the HTTP client, the APScheduler instance, the MCP backing client) are created in the app
  `lifespan` and stored on `app.state`. A small provider reads them back off the request. **Never
  use module-level globals for these** (see §7).
- **Constructor injection everywhere else.** Services and repositories declare their
  collaborators in `__init__` and store them as protected attributes. They import types for
  annotations, not to construct them.
- **Declare reusable `Annotated[...]` dependency aliases** at module top and reuse them across
  handlers, rather than repeating `Annotated[X, Depends(...)]` inline.

### Where it is wired

- **`src/api/app.py`** — `create_api()` builds the `DIALApp`, adds middleware, includes routers,
  and defines the `lifespan` that creates shared components and puts them on `app.state`.
- **`src/api/dependencies/*.py`** — one module per concern (`db.py`, `scheduler.py`,
  `pagination.py`, `auth.py`, …). Each exposes provider functions used with `Depends`.

### Example (provider assembling the graph)

```python
# src/api/dependencies/scheduler.py
def get_scheduler(request: Request) -> AsyncIOScheduler:
    return request.app.state.scheduler  # shared, created in lifespan


async def get_schedule_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    scheduler: Annotated[AsyncIOScheduler, Depends(get_scheduler)],
) -> AsyncGenerator[ScheduleService, None]:
    repository = ScheduleRepository(session)
    run_repository = ScheduleRunRepository(session)
    yield ScheduleService(repository, run_repository, scheduler)
```

### Example (handler + reusable alias)

```python
# src/api/v1/endpoints/schedules.py
ScheduleServiceDep = Annotated[ScheduleService, Depends(get_schedule_service)]


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: str,
    service: ScheduleServiceDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> ScheduleResponse:
    ...
```

`get_schedule` does not import or construct `ScheduleService`; the provider passes it in.

### Example (constructor injection in a service)

```python
class ScheduleService:
    def __init__(
        self,
        repository: ScheduleRepository,
        run_repository: ScheduleRunRepository,
        scheduler: AsyncIOScheduler,
    ) -> None:
        self._repo = repository
        self._run_repo = run_repository
        self._scheduler = scheduler
```

---

## 2. Settings (environment variables)

**All environment-driven configuration lives in pydantic-settings classes. Do not read
`os.getenv` / `os.environ` in application code.** Settings are defined once in
`src/lib/settings.py` and accessed through a single cached accessor.

### Rules

- Use **`pydantic_settings.BaseSettings`**. Group related config into its own class
  (`DatabaseSettings`, `ApiHttpSettings`, `WorkerHttpSettings`) and nest it on the root
  `Settings` with `Field(default_factory=...)`.
- Field names are lower `snake_case`; pydantic-settings maps them case-insensitively to env vars
  (`db_password` ← `DB_PASSWORD`). Use `env_prefix` to namespace a group
  (`ApiHttpSettings` → `API_HTTP_*`). A shared `SettingsConfigDict` (`ENV_FILE_MODEL_CONFIG`)
  centralises the `.env` file, encoding, and `extra="ignore"`.
- Wrap secrets in **`SecretStr`** (e.g. `dial_api_key`) so they are never rendered by accident;
  read them with `.get_secret_value()` only where the raw value is required.
- Enforce cross-field invariants with **`@model_validator(mode="after")`** returning `self`, and
  raise `ValueError` with an actionable message (see `DatabaseSettings.validate_auth_requirements`).
- Access settings via the cached accessor:

  ```python
  @lru_cache
  def get_settings() -> Settings:
      return Settings()
  ```

  Tests reset it with `get_settings.cache_clear()`.
- The **only** place `os.getenv` is acceptable is the logging bootstrap for a third-party knob
  (`OTEL_LOG_LEVEL`), which must be read before `Settings` is available. New app config does not
  follow that exception — add a settings field.

### Example

```python
class DatabaseSettings(BaseSettings):
    model_config = ENV_FILE_MODEL_CONFIG

    db_auth_mode: DbAuthMode = DbAuthMode.PASSWORD
    db_password: str | None = None
    db_url: str | None = None

    @model_validator(mode="after")
    def validate_auth_requirements(self) -> "DatabaseSettings":
        ...
        return self
```

---

## 3. Project layout

Code is split into three top-level packages by runtime role; shared domain code lives in `lib`.

```
src/
├── api/                    # HTTP surface (FastAPI / DIALApp)
│   ├── app.py              #   app factory, middleware, lifespan
│   ├── v1/endpoints/       #   route handlers (thin; translate errors → HTTP)
│   ├── dependencies/       #   Depends providers (DI wiring)
│   └── mcp/                #   MCP server mount
├── worker/                 # APScheduler worker process + executors
│   └── executors/          #   per-target-type execution strategies
└── lib/                    # shared domain code
    ├── settings.py         #   pydantic-settings
    ├── constants.py        #   module-level UPPER_SNAKE_CASE constants
    ├── models/             #   SQLModel ORM tables (persistence shape)
    ├── repositories/       #   data access layer (all DB statements live here)
    ├── services/           #   business logic; orchestrates repos + scheduler
    ├── schemas/            #   Pydantic request/response & internal value objects
    ├── clients/            #   external service clients (e.g. DialClient)
    ├── db/                 #   engine, session, enums, credentials, jobstore
    ├── exceptions/         #   custom exception hierarchy
    └── utils/              #   logging, auth/user-id helpers
tests/                      # mirrors src/ (unit/ + integration/ + e2e/)
migrations/versions/        # Alembic scripts
```

### Layering rules

- **Handlers are thin.** They parse the request, call one service method, and translate domain
  exceptions into `HTTPException`. No business logic, no SQL.
- **Services own business logic.** They orchestrate repositories, the scheduler, and clients;
  they raise domain exceptions (never `HTTPException`).
- **Repositories own data access.** Every SQLAlchemy statement lives here. They return ORM models
  or plain tuples, never Pydantic response schemas.
- **Schemas are the API/internal contract.** Services map ORM models → response schemas (see
  `ScheduleService._to_response`); ORM models never leak out of the service layer as responses.
- **Dependency direction is one-way:** `api` → `services` → `repositories` → `models`/`db`.
  `lib` never imports from `api` or `worker`.

---

## 4. Encapsulation and visibility

- **Protected by default.** Single leading underscore (`_`) for internal attributes, helper
  methods, and module-private helper functions (`_to_response`, `_register_job`, `_safe_body`,
  `_encode_resource_path`). Stored dependencies are protected (`self._repo`, `self._scheduler`).
- **Public only for the intended surface.** Route handlers, service/repository public methods,
  schema fields, and settings fields are public; everything supporting them is protected.
- **`__init__.py` files are empty** — no imports, no re-exports, no code.

---

## 5. Types and clarity

- **Type hints on every signature** — parameters and return types, including `-> None`.
- **Modern built-in generics:** `list[str]`, `dict[str, Any]`, `tuple[Schedule, datetime | None]`.
  Use `X | None`, never `Optional[X]`. Import `AsyncGenerator` from `collections.abc`.
- **Return `None`-or-value explicitly** rather than sentinels: repositories return
  `Schedule | None` / `... | None`; services turn "not found" into a domain exception.
- **`NoReturn`** for helpers that always raise (e.g. `_raise_for_lost_race`).
- **`@staticmethod`** for pure helpers that need no instance state (`_to_response`,
  `_to_next_run_time`, `_order_by_clause`).
- **`StrEnum`** for closed sets of string values (`src/lib/db/enums.py`:
  `TargetType`, `TriggerType`, `ScheduleRunStatus`, `SortDirection`, …). Reference enum members,
  not string literals, in logic.
- Keep unavoidable `# type: ignore[...]` **specific** (name the error code) and, where the reason
  is non-obvious (SQLModel/`kwargs` clash, `__table__` access), add a one-line comment.

---

## 6. Naming

- **Functions, methods, variables, modules:** `snake_case` (`get_schedule_service`,
  `schedule_repository.py`).
- **Classes:** `PascalCase` (`ScheduleService`, `DatabaseSettings`, `DialClient`).
- **Constants:** `UPPER_SNAKE_CASE` in `src/lib/constants.py`
  (`EXECUTE_SCHEDULED_JOB_REF`, `DEFAULT_PAGE_LIMIT`, `API_KEY_HEADER`).
- **Providers:** `get_<thing>` (`get_settings`, `get_scheduler`, `get_db_session`).
- **Dependency aliases:** `<Thing>Dep` (`ScheduleServiceDep`, `PaginationDep`).
- **Domain exceptions:** `<Condition>Error` (`ScheduleNotFoundError`, `JobRemovalError`).
- **Structured log events:** short `snake_case` strings (`schedule_delete_start`,
  `schedule_marked_deleted`) — see §9.

---

## 7. Class bodies and shared state

- **No non-trivial logic in class bodies.** Keep conditionals and computation in `__init__`,
  methods, factory functions, or validators. Class bodies hold field declarations and simple
  literals only.
- **No mutable module-level globals for shared runtime objects.** The HTTP client, scheduler, and
  MCP client live on `app.state`, created in `lifespan`. The one deliberate singleton is
  `Database` (engine/session-maker holder) — it is an explicit `__new__`-based singleton with
  idempotent init, documented as such, not an ambient global.
- Module-level module constants and the lazily-initialised `logger` are fine; mutable process
  state is not.

---

## 8. Pydantic and data containers

- **Use Pydantic v2 `BaseModel`** for API request/response schemas and structured internal values
  (`schemas/`). Validate with `Model.model_validate(...)`; serialise with `.model_dump(...)`.
  Use `mode="json"` when the result must be JSON/JSONB-safe (datetimes → ISO strings), as the
  service does before persisting trigger/properties to JSONB.
- **No mutable defaults.** Use `Field(default_factory=dict)` / `default_factory=list`
  (`metadata`, `payload`, `extra_headers`).
- **Field- and model-level validators** carry input rules: `@field_validator` (with
  `@classmethod`) for single-field checks, `@model_validator(mode="after")` for cross-field rules
  (e.g. `ScheduleTrigger` requiring exactly one of cron/date; `ScheduleProperties` rules that
  depend on `target_type`).
- **`Field(..., description=...)`** where a field is part of the OpenAPI/MCP contract — that text
  is what an MCP agent sees, so keep it meaningful.
- **`dataclasses` are used only for small internal, non-validated value objects / FastAPI
  parameter groups** (`Paginator`, `Pagination`). Anything crossing the API boundary, needing
  validation, or serialised is a Pydantic model. Do not reach for a dataclass where a schema
  belongs.
- **`Protocol`** for structural interfaces implemented by several strategies (e.g. `Executor` in
  `worker/executors/base.py`) — prefer it to an ABC for pluggable behaviour.

---

## 9. Logging and error handling

### Logging

- **Use the shared structlog logger**, never `print()`:

  ```python
  from src.lib.utils.logging import logger
  ```

- **Structured, event-first records.** Pass a short `snake_case` event name as the message and
  attach context as keyword fields:

  ```python
  logger.info("schedule_delete_start", schedule_id=schedule_id, run_count=run_count)
  ```

  The formatting, key ordering, timestamps, and trace/span-id injection are handled centrally in
  `LoggingConfig` / `configure_logging()`.
- **Log metadata, never secrets or payloads.** On the credential paths we log the auth **mode**
  and the exception **type** only (`error_type=type(exc).__name__`), never the exception args.
  `SecretStr` and the OBO/credential flows exist precisely so issued tokens and response bodies do
  not reach logs.

### Log levels

Pick the level from what the record means for the service, not how it reads while developing:

| Level | Meaning | Examples in this codebase |
|---|---|---|
| **DEBUG** | Developer diagnostics; verbose control-flow detail. | intermediate summaries |
| **INFO** | Operational narrative: lifecycle milestones and expected outcomes. | `schedule_persisted`, `schedule_delete_start`, `schedule_deleted`, HTTP client created |
| **WARNING** | Something unexpected happened and was handled; the request continues, possibly degraded, or is retried/rolled back. | job registration failed → row rolled back; race fallback to soft-delete; expected job errors downgraded from APScheduler |
| **ERROR** | A failure that affected the outcome or left stores possibly divergent; each occurrence is worth investigating. | persist failure after the job was already unregistered; row-restore failure after re-registration failure |

- **Log a failure once, at the owning layer.** A layer that recovers, rolls back, retries, or
  re-raises logs at **WARNING or not at all** — the layer that owns the final outcome logs the
  single ERROR. Do not re-log the same failure as it propagates.
- Expected remote/user faults (`ScheduleRunError`) are deliberately downgraded from APScheduler's
  default ERROR to WARNING by `_ExpectedJobErrorFilter`, so only genuine scheduler faults page.

### Error handling — the domain-exception boundary

- **Define domain exceptions** under `src/lib/exceptions/errors.py`, all subclassing
  `SchedulerError`. Give each a docstring stating what it means and how it maps to HTTP. Attach
  correlation/context attributes where useful (`ScheduleRunError.status_code`,
  `OboCredentialsError.response_body`).
- **Services raise domain exceptions; handlers translate them to HTTP.** Handlers never leak raw
  exceptions and never embed SQL/transport text in a client response:

  ```python
  try:
      return await service.get(schedule_id, user_id)
  except ScheduleNotFoundError as exc:
      raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}") from exc
  ```

- **Always chain with `from exc`** (or `from None` when intentionally suppressing).
- **Compensating transactions over silent failure.** When a two-step operation's second step
  fails (row persisted, then job registration fails), roll the first step back and raise — and if
  the compensation itself fails, log ERROR because the stores may diverge (see
  `ScheduleService.create` / `.update`).
- **Let the database be the arbiter of races.** Prefer a single conditional statement
  (`WHERE is_deleted = false`) and inspect `rowcount` over read-then-write checks; resolve a lost
  race by re-reading and raising the correct 404/409 (`_raise_for_lost_race`).
- **Never use bare `assert` for production control flow.**

---

## 10. Imports, linting, and formatting

Configured in `pyproject.toml`; run before committing.

- **ruff** (line-length **120**, `target-version = py314`, double quotes). Lint rules: `E`, `F`,
  `I` (isort).
- **Import order (isort):** standard library → third-party → first-party (`src`), **two blank
  lines after the import block** (`lines-after-imports = 2`), `force-sort-within-sections = true`,
  `combine-as-imports = true`. Let ruff arrange imports; do not hand-order.
- **No wildcard imports.** Import only what you use.
- **mypy** (`python_version = 3.14`, `plugins = ["pydantic.mypy"]`) must pass; keep any
  `# type: ignore` narrowly scoped to a code.

Commands:

```bash
uv run ruff check src tests      # lint
uv run ruff format src tests     # format
uv run mypy src                  # type check
```

---

## 11. Async and I/O

- **All I/O is `async`.** DB sessions (`AsyncSession`), the DIAL client (`httpx.AsyncClient`), and
  service methods are async end-to-end.
- **Bridge blocking APIs with `asyncio.to_thread`.** APScheduler's store operations are
  synchronous, so calls like `add_job` / `remove_job` / `pause_job` are wrapped in
  `asyncio.to_thread(...)` rather than blocking the event loop.
- **Bound external waits.** Credential/connector `close()` calls that can hang are guarded with
  `asyncio.wait_for(..., timeout=...)` and their timeouts logged at WARNING.
- **Sessions come from the provider** (`get_db_session` → `get_session`); repositories accept a
  session and `commit`/`refresh`/`rollback` explicitly — they do not open their own.

---

## 12. Testing

- **pytest with `asyncio_mode = "auto"`** (no per-test `@pytest.mark.asyncio` needed);
  `pytest-mock` for mocking.
- **Tests mirror `src/`** under `tests/`, split into `unit/`, `integration/`, and `e2e/`.
- **Group related cases in `TestXxx` classes**; test method names describe the behaviour
  (`test_password_mode_requires_db_password`).
- **Isolate settings/logging state.** Use `monkeypatch.setenv(...)` for env, and clear the
  settings cache around tests (`get_settings.cache_clear()`); shared fixtures live in
  `conftest.py`.
- Assert on domain outcomes and raised domain exceptions (`pytest.raises(ValueError, match=...)`),
  not on log text.

Commands:

```bash
uv run pytest                                                   # run tests
uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml
```

---

## 13. Dependencies

- **Declare every directly-imported package** in `pyproject.toml` (runtime under
  `[project.dependencies]`, tooling under `[dependency-groups].dev`). Do not rely on transitive
  packages for direct imports.
- **Pin deliberately.** Runtime deps use exact or compatible-range pins; security-motivated
  transitive floors go in `[tool.uv].constraint-dependencies` with a comment naming the CVE and
  the dependency that pulls the vulnerable range.

---

## 14. Summary checklist

- **DI:** handlers get collaborators via `Depends`; providers in `api/dependencies/` build the
  graph; shared runtime objects live on `app.state`; services/repos use constructor injection. No
  DI container, no globals.
- **Settings:** all env config in pydantic-settings classes, accessed via cached `get_settings()`;
  secrets are `SecretStr`; no `os.getenv` in app code.
- **Layering:** thin handlers → services (business logic, domain errors) → repositories (all SQL)
  → models; schemas are the contract; `lib` never imports `api`/`worker`.
- **Visibility:** protected (`_`) by default; public only for the intended surface; empty
  `__init__.py`.
- **Types:** hints everywhere, modern generics, `X | None`, `StrEnum` for closed sets, narrow
  `# type: ignore`.
- **Pydantic:** models for anything crossing the API boundary or needing validation;
  `default_factory` for mutable defaults; dataclasses only for tiny internal value objects.
- **Logging/errors:** structlog event records with metadata (never secrets/payloads); level by
  meaning; log a failure once at the owning layer; domain exceptions raised in services and mapped
  to HTTP in handlers with `from exc`.
- **Async:** async I/O throughout; wrap blocking APScheduler calls in `asyncio.to_thread`; bound
  external waits with timeouts.
- **Tooling:** ruff (120, isort `E`/`F`/`I`) and mypy pass; imports auto-ordered; tests mirror
  `src/` and isolate settings/logging state.
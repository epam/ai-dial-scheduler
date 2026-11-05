# CLAUDE.md — Project Instructions

## Project Overview
Backend for Dial Scheduler. Uses SQLAlchemy/SQLModel with PostgreSQL, async throughout.

## Tech Stack
- **Python 3.14** with **FastAPI**, **APScheduler**
- **SQLAlchemy/SQLModel** ORM (no raw SQL)
- **Alembic** for migrations
- **Pydantic v2** for validation/settings
- **pytest** + pytest-asyncio for testing
- **uv** as package manager
- **ruff** for linting/formatting, **mypy** for type checking

## Commands
- Install deps: `uv sync`
- Lint + format check + type check (same as CI): `make lint`
- Run tests (same as CI): `make test`
- Run tests: `uv run pytest`
- Run tests with coverage: `uv run pytest --cov=src --cov-report=term-missing`
- Lint: `uv run ruff check src tests`
- Format: `uv run ruff format src tests`
- Type check: `uv run mypy src`
- Create migration: `uv run alembic revision --autogenerate -m "description"`
- Run migrations: `uv run alembic upgrade head`

## Code Conventions
- **PEP 8** strictly enforced via ruff (line-length=120)
- `snake_case` for functions/variables/modules, `PascalCase` for classes
- Type hints on all function signatures
- Google-style docstrings on public functions/classes
- Async/await for all I/O operations
- Functions/methods should have max 2-3 parameters; use Pydantic models for more
- Use `src.lib.utils.logging` module `logger`, never `print()`
- Organize imports: stdlib → third-party → local (ruff handles this)
- Never use assert in production code
- Handle reusable components like http client in app state in case of FastApi, do not use globals.
- `__init__.py` files must be empty — no imports, no code, no re-exports

## Project Structure
```
src/
├── api/                # API service (FastAPI)
│   ├── app.py          # App factory, middleware, lifespan
│   ├── v1/endpoints/   # REST route handlers
│   ├── mcp/            # MCP server exposing the REST API as tools
│   └── dependencies/   # FastAPI dependency injection (auth, db, filters, pagination)
├── worker/             # Worker service (APScheduler)
│   ├── app.py          # Worker entrypoint and lifecycle
│   ├── dispatcher.py   # Job dispatch to executors
│   ├── jobs.py         # Scheduled job functions
│   ├── retry.py        # Retry and timeout handling
│   └── executors/      # Target executors: chat_completion, responses, rest, sse
└── lib/                # Shared code; MUST NOT import from src.api or src.worker
    │                   #   (known exception: services/run_launcher.py imports src.worker.jobs)
    ├── settings.py     # Pydantic settings
    ├── constants.py    # Application constants
    ├── scheduler.py    # APScheduler setup
    ├── clients/        # External service clients (DIAL)
    ├── db/             # Database engine, credentials, enums
    ├── models/         # SQLAlchemy/SQLModel ORM models
    ├── repositories/   # Data access layer (repository pattern)
    ├── schemas/        # Pydantic schemas (API request/response, internal)
    ├── services/       # Business logic layer
    ├── exceptions/     # Custom exception hierarchy
    └── utils/          # Logging, user id helpers
tests/                  # unit/ (mirrors src/), integration/ (flat, needs TEST_DB_URL), e2e/ (.http files), helpers/
migrations/versions/    # Alembic migration scripts
```

## Principles
- SOLID, DRY, KISS, YAGNI, Clean Architecture
- Only add comments for complex logic; code should be self-explanatory
- All comments in English
- Comprehensive unit tests for new code

## Security
- **NEVER** read `.env`, `.envrc`, or any `secrets.env` files
- Do not log or expose secrets, tokens, or credentials
- Do not use `print()` for debugging

## Testing
- Use `pytest` with async support (`asyncio_mode = "auto"`)
- Test files mirror `src/` structure under `tests/`
- Use `pytest-mock` for mocking

<!-- SPECKIT START -->
## Active Feature Plan
- Current plan: the `plan.md` of the highest-numbered directory under `specs/` that is still in progress
<!-- SPECKIT END -->

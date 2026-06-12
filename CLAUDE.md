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
- Run tests: `uv run pytest`
- Run tests with coverage: `uv run pytest --cov=src --cov-report=xml:coverage.xml --junitxml=report.xml`
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
- Use `src.utils.logging` module `logger`, never `print()`
- Organize imports: stdlib → third-party → local (ruff handles this)
- Never use assert in production code
- Handle reusable components like http client in app state in case of FastApi, do not use globals.
- `__init__.py` files must be empty — no imports, no code, no re-exports

## Project Structure
```
src/
├── app.py              # FastAPI app factory, middleware, lifespan
├── settings.py         # Pydantic settings (DB, Redis, DIAL auth, etc.)
├── constants.py        # Application constants
├── api/
│   ├── v1/endpoints/   # API route handlers
│   ├── v1/interceptors/ # Request/response interceptors
│   └── dependencies/   # FastAPI dependency injection
├── clients/            # External service clients (Redis, chunk store)
├── db/                 # Database engine, session, enums
├── models/             # SQLAlchemy/SQLModel ORM models
├── repositories/       # Data access layer (repository pattern)
├── schemas/            # Pydantic schemas (API request/response, internal)
├── services/           # Business logic layer
├── exceptions/         # Custom exception hierarchy
└── utils/              # Logging, auth utilities, HTTP helpers
tests/                  # Mirrors src/ structure
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
- Current plan: `specs/012-schedule-pause-resume/plan.md` (Schedule Pause & Resume Endpoints)
<!-- SPECKIT END -->

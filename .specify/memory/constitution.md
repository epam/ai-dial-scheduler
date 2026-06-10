<!--
SYNC IMPACT REPORT
==================
Version change: 1.1.0 → 1.2.0

Bump rationale: MINOR — Code Conventions > Project Structure materially updated.
  api/ and worker/ are now explicit top-level service entry points. Shared domain
  logic, settings, constants, and utilities move into lib/ (no imports from api/
  or worker/ permitted). Layer dependency rules documented.

Modified principles: none (I–V titles and intent unchanged)

Added sections: none

Updated sections:
  - Code Conventions > Project Structure — split into api/ + worker/ + lib/

Removed sections: none

Templates reviewed:
  - .specify/templates/plan-template.md         ✅ aligned — Constitution Check gate present;
                                                   plan Source Code tree options use generic
                                                   paths and will be filled per-feature
  - .specify/templates/spec-template.md         ✅ aligned — FR/SC structure unchanged
  - .specify/templates/tasks-template.md        ✅ aligned — path conventions section uses
                                                   generic placeholders; per-feature tasks
                                                   will reference real paths
  - .specify/templates/constitution-template.md ✅ source template, no changes needed

Follow-up TODOs:
  - worker/ internal structure (entry point, job definitions) is not yet defined.
    Document once first worker job is implemented.
  - DIAL API reference (https://dialx.ai/dial_api) could not be fetched (JS-rendered).
    Review and add DIAL-specific auth/integration constraints when accessible.
-->

# AIDIAL Scheduler Constitution

## Project SDLC (NON-NEGOTIABLE)

Software Development should stick to next lifecycle, top level actions are triggered by user and guided by agent:

1) User runs /speckit-specify, this command should run with most capable available LLM (like opus)
2) User runs /speckit-plan in same session, this command should run with most capable available LLM (like opus)
3) User runs /speckit-implement in separate terminal, providing spec folder as an argument,
   this command should run with middle capabilities LLM (like sonnet):
    - before execution agent should create unit tests using TDD approach;
    - after implementation main agent should run uint tests and fix failing tests
    - after implementation main agent should run ruff and fix found issues if them are not fixable automatically by ruff itself
    - after implementation main agent should run mypy against src and test folders and fix found issues
    - when all issues are fixed main agent should spawn code-review-agent, code-review-agent should not see main agent context
    - after code-review-agent execution main agent should read produced code review report from spec directory, address comments and run code-review-agent
    - repeat running code-review-agent until all issues are resolved
    - run all quality gates
    - create integration tests
    - run integration tests

## Core Principles

### I. Reliability First

All provided solutions should correspond to industry standards and best practices.

Written code should be reliable and should take into account:
- error handling,
- thread safety,
- correct i/o bound tasks execution with no blocking event loop,
- loosing data prevention,
- proper retry and timeout handling.


### II. Observability (NON-NEGOTIABLE)

Every scheduling decision (enqueue, dequeue, delay, retry, discard) MUST emit a structured log entry via `src.lib.utils.logging` (`logger`).

Log entries MUST include: task ID, tenant/model identifier, decision type, ISO-8601 UTC timestamp, and reason code. `print()` is explicitly forbidden.
OTLP Traces produced by AIDialSdk telemetry should produce

**Rationale**: Scheduler bugs are notoriously hard to reproduce. Without full traceability
from enqueue to execution, diagnosing priority inversions or starvation is impractical.

Use DialApp from AiDialSdk as FasApi wrapper for api services, it provides builtin tracing and metrics
Use direct init_telemetry method from AiDialSdk non-api services, to get same built in tracing and metrics.

All task runs should be persisted in database, and contain data both about successful and failed runs and all attempts.


### III. Test-First (NON-NEGOTIABLE)

TDD is mandatory: tests MUST be written and confirmed failing before implementation begins.
The Red-Green-Refactor cycle MUST be enforced. Unit tests cover pure logic; integration tests
cover scheduler-to-queue and scheduler-to-downstream interactions; contract tests guard
API/message-schema boundaries. No task may be marked complete if its associated tests do
not pass. Tests run with `uv run pytest`.

**Rationale**: Schedulers carry subtle timing and ordering invariants. Tests written after
the fact tend to miss edge cases (e.g., clock skew, concurrent dispatch) that cause
production incidents.

### IV. Contract-Driven API

Every external interface exposed by the API service (REST endpoint, request/response schema,
configuration file format) MUST have a versioned contract (FastAPI auto-generated OpenAPI or
explicit JSON Schema) committed alongside the implementation. Breaking changes MUST bump the
contract's major version. Internal interfaces shared between the API service and the
background worker MUST be expressed as explicit Python typed interfaces or Pydantic v2
models — no ad-hoc dict passing across service boundaries.

**Rationale**: The API and background worker are independently deployable services. Implicit
contracts between them become integration failures under independent releases.

### V. Simplicity & YAGNI

The simplest solution that satisfies the current specification MUST be preferred. Abstractions
MUST only be introduced when at least two concrete use cases demonstrably benefit. Functions
and methods MUST have at most 2–3 parameters; group additional data into Pydantic models.
Complexity MUST be documented in the plan's Complexity Tracking table. Framework magic that
obscures control flow is explicitly discouraged.

**Rationale**: Premature generalization in scheduling systems often introduces subtle ordering
bugs and makes the system harder to reason about under failure conditions.

## Technical Constraints

- **Language**: Python 3.14. Type annotations are REQUIRED on all public interfaces and all
  function signatures. `mypy` type-checking (`uv run mypy src`) MUST pass before merge.
- **Web framework**: FastAPI. Reusable components (HTTP clients, etc.) MUST be held in
  FastAPI application state; module-level globals are forbidden.
- **Scheduling engine**: APScheduler (background worker service).
- **ORM**: SQLAlchemy/SQLModel — no raw SQL. Database: PostgreSQL.
- **Migrations**: Alembic (`uv run alembic upgrade head`). Every schema change requires an
  autogenerated migration (`uv run alembic revision --autogenerate -m "description"`).
- **Validation & settings**: Pydantic v2 for all request/response schemas and application
  settings.
- **Package manager**: `uv`. Lockfile MUST be committed. Install: `uv sync`.
- **Linting/Formatting**: `ruff` (line-length=120, PEP 8 strict). Both `ruff check` and
  `ruff format` violations are CI gate blockers.
- **Containerization**: Both the API service and the background worker MUST be deployable as
  Docker containers. A `Dockerfile` is a required deliverable for each service.
- **Security**: `.env`, `.envrc`, and `secrets.env` files MUST NEVER be read by code.
  No secrets, tokens, or credentials may be logged or printed. All credentials MUST be
  injected via environment variables or a secrets manager. Dependencies MUST be scanned for
  known CVEs before merge.
- **Async**: `async`/`await` is REQUIRED for all I/O operations; `assert` is forbidden in
  production code.
- **DIAL platform**: The API service integrates with the DIAL platform. DIAL auth headers
  and API conventions MUST be honoured; see `src/lib/settings.py` (DIAL auth config) and
  `src/lib/utils/` (auth utilities) for integration points.

## Code Conventions

- **Naming**: `snake_case` for functions, variables, modules; `PascalCase` for classes.
- **Imports**: stdlib → third-party → local (enforced by ruff).
- **Docstrings**: Google-style on all public functions and classes.
- **Comments**: Only for complex logic whose intent is non-obvious. All comments in English.
- **Logging**: Use `src.lib.utils.logging` `logger` exclusively — never `print()`.
- **Project structure** (source of truth):

  ```
  src/
  ├── api/                      # FastAPI service entry point
  │   ├── app.py                # App factory, middleware, lifespan
  │   ├── v1/
  │   │   ├── endpoints/        # Route handlers
  │   │   └── interceptors/     # Request/response interceptors
  │   └── dependencies/         # FastAPI dependency injection
  ├── worker/                   # APScheduler background worker entry point
  └── lib/                      # Shared library — no imports from api/ or worker/
      ├── models/               # SQLAlchemy/SQLModel ORM models
      ├── repositories/         # Data access layer (repository pattern)
      ├── services/             # Business logic layer
      ├── schemas/              # Pydantic schemas (API + internal)
      ├── clients/              # External service clients (Redis, etc.)
      ├── db/                   # Database engine, session, enums
      ├── exceptions/           # Custom exception hierarchy
      ├── settings.py           # Pydantic settings (DB, Redis, DIAL auth, etc.)
      ├── constants.py          # Application constants
      └── utils/                # Logging, auth utilities, HTTP helpers
  tests/                        # Mirrors src/ structure
  migrations/versions/          # Alembic migration scripts
  ```

  **Layer rules**:
  - `api/` and `worker/` depend on `lib/`.
  - `lib/` MUST NOT import from `src.api` or `src.worker`; the dependency arrow flows inward only.
  - Circular imports across layers are forbidden and are a CI gate blocker.

## Development Workflow

- All work starts from a feature branch named `NNN-short-description` off `main`.
- Pull requests MUST reference the originating spec and pass all CI gates before merge.
  CI gates: `uv run ruff check src tests`, `uv run ruff format --check src tests`,
  `uv run mypy src`, `uv run pytest --cov=src`.
- Each PR MUST include or update the relevant contract/schema if an API interface changed.
- Code review MUST verify: principle compliance, test coverage (all new code covered),
  observability instrumentation, absence of reliability hazards (unhandled errors, missing
  retries), and absence of secrets in code or logs.
- Releases follow SemVer: `MAJOR.MINOR.PATCH`. The changelog MUST be updated for every
  release that affects the public API or observable behaviour.

## Governance

This constitution supersedes all informal practices and ad-hoc agreements. Amendments require:

1. A written proposal (can be a PR description) stating what changes and why.
2. Review and explicit approval by at least one additional team member.
3. Version bump of this document following semantic versioning rules:
   - **MAJOR**: Removal or redefinition of an existing principle.
   - **MINOR**: Addition of a new principle or section, or material update of constraints.
   - **PATCH**: Clarifications, wording improvements, non-semantic edits.
4. Update of all dependent templates (see Sync Impact Report format above).

All PRs and design reviews MUST include a Constitution Check verifying compliance with the
five core principles. Complexity violations MUST be documented in the plan's Complexity
Tracking table — undocumented violations are grounds for blocking a PR.

**Version**: 1.2.0 | **Ratified**: 2026-06-05 | **Last Amended**: 2026-06-08

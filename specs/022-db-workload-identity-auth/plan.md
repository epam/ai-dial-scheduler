# Implementation Plan: Workload-Identity Database Authentication

**Branch**: `022-db-workload-identity-auth` | **Date**: 2026-06-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/022-db-workload-identity-auth/spec.md`

## Summary

Add two secretless database authentication modes — `gcp_iam` and `azure_entra` — alongside the
existing `password` and `connection-string` modes. The selected mode is driven by the existing
`DB_AUTH_MODE` setting and must apply uniformly to **every** PostgreSQL connection the service
opens: the async request-serving engine (asyncpg), the synchronous APScheduler `SQLAlchemyJobStore`
engine, and Alembic migrations.

Technical approach:

- **`gcp_iam`** reuses the proven Cloud SQL Python Connector pattern from the sibling
  `ai-agent-manager-backend` service: the async engine is built with `create_async_engine(...,
  async_creator=...)` where the creator calls `Connector.connect_async(..., "asyncpg",
  enable_iam_auth=True)`. The connector does **not** support psycopg2, so the synchronous job-store
  / Alembic engine for this mode uses the connector's **pg8000** sync driver
  (`create_engine("postgresql+pg8000://", creator=...)`).
- **`azure_entra`** acquires a Microsoft Entra access token (scope
  `https://ossrdbms-aad.database.windows.net/.default`) from the workload's managed identity via
  `azure-identity`, and uses it as the connection password (username = Entra principal). Because
  tokens are short-lived (5–60 min), a fresh token is fetched per new connection through a connect
  hook — `async_creator` for asyncpg and a SQLAlchemy `do_connect` event listener for psycopg2.
- The `password` and `connection-string` modes are untouched and remain the default.

The shared engine/job-store construction logic is centralized so the API service, the worker, and
Alembic all resolve connections through the same mode-aware factory (removing the current
duplicated job-store wiring between `src/lib/scheduler.py` and `src/worker/app.py`).

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI / `aidial-sdk`, APScheduler 3.11, SQLAlchemy 2.0.50 (`[asyncio]`),
SQLModel, Alembic, asyncpg, psycopg2; **new**: `cloud-sql-python-connector[pg8000]` (GCP IAM, sync
+ async drivers), `azure-identity` (Entra token acquisition, sync + `aio` async credentials)

**Storage**: PostgreSQL (Cloud SQL on GCP; Azure Database for PostgreSQL on Azure)

**Testing**: pytest + pytest-asyncio + pytest-mock (`uv run pytest`)

**Target Platform**: Linux server containers (API service + background worker), deployed on GKE/
Cloud Run (GCP) or AKS/Container Apps (Azure)

**Project Type**: Web service + background worker (single repo, `src/api`, `src/worker`, shared
`src/lib`)

**Performance Goals**: No added per-request latency in steady state; credential acquisition must be
cached/refreshed out of the hot path (token reuse until near expiry). Engine initialization remains
a one-time startup cost.

**Constraints**: Tokens/credentials/passwords MUST NEVER be logged. Token acquisition is network
I/O and MUST NOT block the event loop on the async path (use `azure.identity.aio`). Backward
compatibility with `password`/`connection-string` is mandatory. No raw SQL. `mypy` clean.

**Scale/Scope**: Connection-establishment concern only; no change to schemas, endpoints, or job
semantics. Touch points: `src/lib/db/`, `src/lib/settings.py`, `src/lib/scheduler.py`,
`src/worker/app.py`, `migrations/env.py`, `pyproject.toml`, `Dockerfile`.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|-----------|-----------|
| **I. Reliability First** | PASS — short-lived credentials refreshed automatically per connection; fail-fast settings validation; `pool_pre_ping` already enabled; clear error surfacing on credential/identity failure. |
| **II. Observability** | PASS — engine init already logs the selected `db_auth_mode`; new paths add structured log entries for credential acquisition outcome (success/failure, mode) with **no secret values**. No new scheduling decisions introduced. |
| **III. Test-First** | PASS — unit tests (settings validation per mode, URL/engine factory selection, connect-hook token injection with mocked connector/credential) written before implementation. Cloud auth itself validated via quickstart in-cloud (out of unit scope). |
| **IV. Contract-Driven API** | PASS — no external REST contract changes. The configuration contract (settings + required fields per mode) is documented in `contracts/db-auth-config.md`; internal interfaces expressed as typed Pydantic settings, no ad-hoc dicts. |
| **V. Simplicity & YAGNI** | PASS — only the two requested modes added; shared factory removes existing duplication rather than adding abstraction layers; functions keep ≤3 params by passing `DatabaseSettings`. |
| **Tech constraints** (async I/O, no raw SQL, mypy, ruff, Docker, no secret logging) | PASS — async token credential on async path; deps added via `uv`/lockfile and Dockerfile; secrets never logged. |

**Result**: PASS — no violations. Complexity Tracking table not required.

## Project Structure

### Documentation (this feature)

```text
specs/022-db-workload-identity-auth/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── db-auth-config.md # Configuration contract (settings + required fields per mode)
└── checklists/
    └── requirements.md   # Spec quality checklist (from /speckit-specify)
```

### Source Code (repository root)

```text
src/
├── api/
│   └── dependencies/db.py        # unchanged (consumes shared session)
├── worker/
│   └── app.py                    # MODIFIED — use shared job-store factory
└── lib/
    ├── settings.py               # MODIFIED — add GCP/Azure settings + validation
    ├── scheduler.py              # MODIFIED — use shared job-store factory
    └── db/
        ├── enums.py              # MODIFIED — add GCP_IAM, AZURE_ENTRA modes
        ├── url.py                # MODIFIED — sync-URL helpers (no-password variants)
        ├── engine.py             # MODIFIED — async engine: branch by mode (asyncpg/IAM/Entra)
        ├── sync_engine.py        # NEW — mode-aware sync Engine factory (pg8000/psycopg2 + hooks)
        ├── jobstore.py           # NEW — build_jobstore(settings) → SQLAlchemyJobStore
        ├── credentials.py        # NEW — GCP Connector holder + Azure token providers (sync/async)
        └── session.py            # unchanged

migrations/
└── env.py                        # MODIFIED — build sync engine via shared factory

tests/unit/lib/
├── test_db_url.py                # MODIFIED — new sync-URL helper cases
├── test_settings_db_auth.py      # NEW — per-mode required-field validation
├── test_db_engine_selection.py   # NEW — async engine branch selection (mocked)
├── test_sync_engine.py           # NEW — sync engine branch + token-injection hook (mocked)
└── test_jobstore.py              # NEW — job-store uses url= vs engine= per mode
```

**Structure Decision**: Single-repo web-service + worker layout (already established). All new code
lives in the shared `src/lib/db/` package so both `src/api` and `src/worker` (and `migrations/`)
consume identical connection logic, honoring the constitution's inward-only dependency rule
(`lib/` never imports from `api/`/`worker/`). New modules are introduced only where the existing
files would otherwise mix unrelated concerns (sync vs async engine construction, credential
lifecycle, job-store assembly).

## Complexity Tracking

> No constitution violations — table intentionally omitted.

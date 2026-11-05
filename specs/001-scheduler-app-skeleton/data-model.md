# Data Model: Scheduler Application Skeleton

**Feature**: `001-scheduler-app-skeleton` | **Date**: 2026-06-09

---

## Overview

The skeleton establishes two layers of persistence:

1. **Application schema** — Alembic-managed tables for `BaseSQLModel` subclasses. No application models are defined yet in this skeleton; only the base class and metadata configuration are created.
2. **APScheduler jobstore** — APScheduler 3.x creates and manages its own `apscheduler_jobs` table independently. This table is NOT managed by Alembic.

---

## Layer 1: Application Schema (Alembic-managed)

### `BaseSQLModel`

**Module**: `src/lib/models/base.py`

The abstract base class inherited by all future application models.

| Attribute | Type | Constraints | Notes |
|-----------|------|-------------|-------|
| `id` | `int` | PRIMARY KEY, auto-increment | Surrogate key |
| `created_at` | `datetime` (timezone-aware) | NOT NULL, server default `now()` | Set by the database at insert time |
| `updated_at` | `datetime` (timezone-aware) | NOT NULL, server default `now()`, `onupdate now()` | Updated by the database on every row modification |

**Naming convention** (enforced via SQLAlchemy `MetaData`):

| Constraint type | Pattern |
|-----------------|---------|
| Index | `IX_<column_label>` |
| Unique | `UQ_<table>_<column>` |
| Check | `CK_<table>_<constraint_name>` |
| Foreign key | `FK_<table>_<column>_<referenced_table>` |
| Primary key | `PK_<table>` |

**Table naming**: `__tablename__` is auto-derived from the class name via `CamelCase → snake_case + "s"` (e.g., `ScheduledJob` → `scheduled_jobs`). Subclasses may override `__tablename__` explicitly.

**No migrations are generated in this skeleton.** The `migrations/versions/` directory starts empty because no `BaseSQLModel` subclasses exist yet.

---

## Layer 2: APScheduler Jobstore (APScheduler-managed)

### `apscheduler_jobs` table

APScheduler 3.x `SQLAlchemyJobStore` creates this table automatically on `scheduler.start()` if it does not exist. It is NOT touched by Alembic.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `VARCHAR(191)` | Job ID (APScheduler-assigned string) |
| `next_run_time` | `FLOAT` | Unix timestamp; NULL for paused jobs |
| `job_state` | `BYTEA` | Pickled job state (APScheduler internal) |

**Important**: This table is managed exclusively by APScheduler. Do not reference it in Alembic migrations. Do not query it directly in application code — use the APScheduler scheduler API.

---

## Planned Application Entity: Job

The `Job` entity is defined in the spec as a future model that will be implemented when scheduling routes are added. It is documented here as a design reference.

**Planned module**: `src/lib/models/job.py` (future feature)

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `id` | `int` | PK (from BaseSQLModel) | |
| `kind` | `str` | NOT NULL | Identifies the job type/handler |
| `status` | `JobStatus` (enum) | NOT NULL, default `PENDING` | Current lifecycle state |
| `scheduled_at` | `datetime` (tz-aware) | NOT NULL | When the job is scheduled to run |
| `created_at` | `datetime` (tz-aware) | NOT NULL (from BaseSQLModel) | |
| `updated_at` | `datetime` (tz-aware) | NOT NULL (from BaseSQLModel) | |

**Planned status enum** (`src/lib/db/enums.py` or `src/lib/models/enums.py`):

```
PENDING → RUNNING → COMPLETED
                  → FAILED
```

| Status | Description |
|--------|-------------|
| `PENDING` | Job created, not yet picked up by worker |
| `RUNNING` | Worker has started executing the job |
| `COMPLETED` | Job finished successfully |
| `FAILED` | Job terminated with an error |

**Note**: This entity is out of scope for the skeleton. It will be introduced in the next feature when scheduling routes and job execution logic are added. No migration is generated here.

---

## Schema Relationships (Skeleton)

```
┌─────────────────────────────┐    ┌──────────────────────────────┐
│  Alembic-managed (app)      │    │  APScheduler-managed         │
│                             │    │                              │
│  (no tables yet)            │    │  apscheduler_jobs            │
│  BaseSQLModel base ready    │    │  (auto-created on start)     │
└─────────────────────────────┘    └──────────────────────────────┘
```

The two stores are independent. The app connects to them using separate database URLs:
- **Async engine** (`postgresql+asyncpg://...`) → app queries (future)
- **Sync URL** (`postgresql+psycopg2://...`) → APScheduler jobstore only

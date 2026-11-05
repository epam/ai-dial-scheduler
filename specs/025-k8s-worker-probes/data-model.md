# Data Model: Kubernetes Probes for Worker Service

This feature introduces no persisted (database) entities and no new database schema/migration.
The only "entities" are filesystem marker files (checked via Kubernetes `exec` probes) and the
in-process state that maintains them.

## Marker Files (`src/worker/health.py`)

All paths live under a single configurable directory, `worker_health_dir` (default `/tmp`,
matching the Kubernetes docs' own example). Each is a zero-byte file; only its **existence** and,
for `heartbeat`, its **mtime**, carry meaning. No file content is read or written.

| File        | Created by                                                                 | Removed by                                    | Meaning when present / fresh                        |
|-------------|-----------------------------------------------------------------------------|--------------------------------------------------|-------------------------------------------------------|
| `started`   | Worker startup, once, immediately after `scheduler.start()` succeeds and the heartbeat job is registered. | Never removed during the process's life (deleted only when the container's `/tmp` is recreated on restart). | Startup has completed — gate for the `startupProbe`. |
| `heartbeat` | Worker startup (once, immediately, so liveness is healthy before the first tick) and every successful run of the existing `worker_heartbeat` APScheduler job (`src/worker/jobs.py::heartbeat`). | Never explicitly removed; staleness is judged by mtime, not existence. | mtime age `<= worker_liveness_staleness_seconds` (default 180s = `3 * noop_interval_seconds`) → alive. |

The `worker_heartbeat` job runs in a dedicated in-memory APScheduler jobstore (alias
`"heartbeat"`), separate from the DB-backed `"default"` jobstore used for user schedules — see
`research.md` §7a. This was required after live testing showed a database outage otherwise stops
APScheduler from marking *any* job in the DB jobstore as due, which would have coupled liveness
to database availability and violated FR-007.
| `ready`     | The readiness poller task, each cycle where `check_database(readiness_db_timeout_seconds)` succeeds, provided the worker has completed startup and is not shutting down. | The readiness poller (when a DB check cycle fails) and immediately, unconditionally, on receipt of SIGTERM/SIGINT (before scheduler shutdown begins). | Worker is initialized, DB-reachable, and not shutting down — gate for the `readinessProbe`. |

At the very start of the worker process (before scheduler creation), all three files are removed
if present, so a stale file left over from a previous crash inside the same container does not
produce a false-positive probe result.

## ReadinessPoller (in-memory, `src/worker/health.py`)

An `asyncio.Task`, started once the scheduler has started and the heartbeat job is registered
(same point the `started` file is touched), running until the worker's stop event is set.

| Field                                       | Type    | Description                                                                 |
|-----------------------------------------------|---------|--------------------------------------------------------------------------------|
| `interval_seconds` (`worker_readiness_check_interval_seconds`) | `float` | How often the poller checks DB connectivity (default 15s). |
| `db_timeout_seconds` (`readiness_db_timeout_seconds`, existing setting) | `float` | Timeout passed to `check_database`, shared with the API's `/ready` check (default 2.0s). |

**Behavior per cycle**: `await check_database(db_timeout_seconds)` → touch `ready` on success,
remove `ready` on failure (logged via `logger.warning("readiness_check_failed", ...)`, mirroring
the existing API implementation).

## Existing entities reused (no schema change)

- **Schedule**, **ScheduleRun** (`src/lib/models/`) — unaffected; the readiness check performs a
  stateless `SELECT 1` via the shared `check_database` helper, not a query against these tables.

## Shared helper (`src/lib/services/health_check.py`)

| Function                                        | Description                                                                                     |
|--------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| `check_database(timeout: float) -> bool`         | Runs `SELECT 1` via `Database().sessionmaker()` bounded by `timeout`; returns `True`/`False`. Used by both the API's `GET /ready` endpoint and the worker's readiness poller — single source of truth for DB-connectivity semantics. |

# Implementation Plan: Kubernetes Probes for Worker Service

**Branch**: `025-k8s-worker-probes` | **Date**: 2026-07-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/025-k8s-worker-probes/spec.md`

## Summary

The worker service (`src/worker/app.py`) currently exposes no HTTP/TCP/gRPC surface, so
Kubernetes has no way to tell whether a running worker instance is alive, ready, or still
starting up. Following the Kubernetes documentation's recommended pattern for listener-less
containers, this plan has the worker maintain three filesystem marker files
(`started`, `heartbeat`, `ready`) under a configurable directory (default `/tmp`), checked via
Kubernetes `exec` probes rather than adding a new network listener. `heartbeat` freshness (driven
by the existing `worker_heartbeat` APScheduler job) backs liveness; `ready` (driven by a new
periodic DB-connectivity poller, using a database-check helper extracted from the API's existing
`GET /ready` into shared `src/lib`) backs readiness; `started` backs the startup probe. Recommended
`exec` probe YAML is documented for the external Helm chart/deploy repo that actually defines the
Kubernetes manifests.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: APScheduler (existing), Python stdlib `os`/`pathlib` for marker-file
I/O — no new HTTP/ASGI dependency is introduced for the worker's health surface

**Storage**: PostgreSQL (via SQLAlchemy/SQLModel, existing `src.lib.db.engine.Database`); marker
files live on the container's local filesystem (default `/tmp`), not persisted storage

**Testing**: pytest + pytest-asyncio (`uv run pytest`)

**Target Platform**: Linux containers orchestrated by Kubernetes (worker deployed via the
existing single Docker image, Alpine-based, with `SERVICE=worker`); Kubernetes `exec` probes run
inside the container via its existing POSIX shell/BusyBox utilities — no new packages required

**Project Type**: Backend service — single repo, `api/` + `worker/` + shared `lib/` (per
constitution's Project Structure)

**Performance Goals**: Marker-file writes and the periodic DB-connectivity poll must add
negligible overhead and never block job dispatch on the scheduler's event loop; `exec` probes are
comparatively expensive (fork per tick per the Kubernetes docs), so probe periods are chosen to
keep that cost low (see `contracts/worker-health-probes.md`).

**Constraints**: No inbound network surface added to the worker — health is observable purely via
the container's local filesystem, consistent with the worker's current no-listener design. Must
not introduce Redis or any new hard dependency. Must work under the existing `db_auth_mode`
variants (password, connection string, GCP IAM, Azure Entra) already supported by `Database`.

**Scale/Scope**: Single worker process per pod (current deployment model); one liveness signal,
one readiness signal, reusing them for the startup probe. No change to job-scheduling behavior
itself.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Reliability First**: Marker-file writes and the readiness DB poll are timeout-bounded
  (reuse `readiness_db_timeout_seconds`) and run on the same asyncio event loop without blocking
  scheduler I/O; shutdown handling explicitly flips readiness off before draining in-flight work
  — PASS.
- **II. Observability (NON-NEGOTIABLE)**: Readiness poll failures/recoveries logged via
  `src.lib.utils.logging.logger` (no `print()`), mirroring the existing API readiness log
  message; this feature does not add scheduling decisions (enqueue/dequeue/retry) so no new
  decision log fields are required — PASS.
- **III. Test-First (NON-NEGOTIABLE)**: Tasks will specify unit tests for marker-file
  read/write helpers, the readiness poller, and the shared DB-check helper, written before
  implementation — addressed in `/speckit-tasks` and enforced during `/speckit-implement` per the
  project's TDD workflow — PASS (procedural, not a design gate).
- **IV. Contract-Driven API**: The worker's probe interface (files + exec commands) gets an
  explicit contract (`contracts/worker-health-probes.md`), analogous to the existing `/health`
  and `/ready` contract on the API service, even though it is filesystem- rather than
  HTTP-based — PASS.
- **V. (Simplicity, per Technical Constraints/Code Conventions)**: Follows the Kubernetes docs'
  own recommended pattern for listener-less workers instead of adding a new ASGI app/port purely
  for probes; reuses the existing DB-check logic rather than duplicating it — PASS.

No violations requiring the Complexity Tracking table.

## Project Structure

### Documentation (this feature)

```text
specs/025-k8s-worker-probes/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md         # Phase 1 output (/speckit-plan command)
├── contracts/
│   └── worker-health-probes.md
└── tasks.md              # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/
├── api/
│   └── v1/endpoints/health.py       # Existing /health, /ready — DB-check logic extracted from here
├── worker/
│   ├── app.py                       # Modified: create marker-file dir, start readiness poller task, wire shutdown
│   ├── health.py                    # NEW: marker-file helpers (started/heartbeat/ready) + readiness poller task
│   └── jobs.py                      # Modified: heartbeat() touches the heartbeat marker file
└── lib/
    ├── services/
    │   └── health_check.py          # NEW: shared check_database(timeout) used by api + worker
    └── settings.py                  # Modified: add worker_health_dir, worker_liveness_staleness_seconds,
                                      #           worker_readiness_check_interval_seconds

tests/
├── unit/api/v1/endpoints/test_health.py     # Existing, updated for shared helper import path
├── unit/lib/services/test_health_check.py   # NEW
└── unit/worker/test_health.py               # NEW
```

**Structure Decision**: Follows the existing `api/` + `worker/` + `lib/` layering mandated by the
constitution. The DB-connectivity check moves from `src/api/v1/endpoints/health.py` into
`src/lib/services/health_check.py` so it is shared without `worker/` importing from `api/`
(forbidden) or vice versa. The worker gets its own small `src/worker/health.py` module (marker-file
I/O + the readiness poller task) rather than an ASGI app, since the worker adds no network
listener — health is observed purely through the container's local filesystem via Kubernetes
`exec` probes, per the pattern in `research.md`.

## Complexity Tracking

*No violations — table intentionally omitted.*

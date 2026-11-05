# Phase 0 Research: Kubernetes Probes for Worker Service

All unknowns from the spec's Assumptions section are resolved below; no
`[NEEDS CLARIFICATION]` markers remain.

> Reference consulted: [Configure Liveness, Readiness and Startup Probes — Kubernetes docs](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)

## 1. How should the worker expose health signals to Kubernetes?

- **Decision**: Use `exec` probes (no new listener/port). The worker maintains three
  filesystem marker files under a configurable directory (default `/tmp`, matching the
  Kubernetes docs' own example): `started`, `heartbeat`, `ready`. Probes run shell one-liners
  inside the container checking file existence/freshness.
- **Rationale**: This is the pattern the Kubernetes documentation demonstrates first and
  recommends specifically for containers with no HTTP/TCP/gRPC listener — which describes this
  worker exactly (it accepts no inbound traffic today; adding a listener purely for probes would
  be a new attack surface and a new operational concern — a port to secure, monitor, and keep out
  of any Service definition by mistake). It also keeps the change minimal: no new ASGI app, no
  new port to coordinate with the external Helm chart beyond three `exec` commands.
- **Alternatives considered**:
  - *A minimal FastAPI/uvicorn health server on its own port* (`httpGet` probes): rejected for
    this worker — richer JSON responses aren't needed for a probe consumed only by kubelet, and
    it adds a listening port to a process that otherwise has zero inbound surface. The Kubernetes
    docs themselves list this as a "lightweight alternative," not the default recommendation, for
    genuinely listener-less workers.
  - *TCP-socket probe against an arbitrary port*: rejected — same reasoning as spec Edge Cases:
    can't distinguish "process alive" from "scheduling loop hung."

## 2. What should the liveness signal be based on?

- **Decision**: A `heartbeat` file, touched (`os.utime`/`Path.touch()`) each time the existing
  `worker_heartbeat` APScheduler job (`src/worker/jobs.py::heartbeat`, interval
  `noop_interval_seconds`, default 60s) runs successfully, and once immediately at worker startup
  (before the first scheduled tick) so liveness is healthy from process start. The `livenessProbe`
  `exec` command checks the file's mtime age against `worker_liveness_staleness_seconds`
  (default `180` = `3 * noop_interval_seconds`).
- **Rationale**: Identical detection logic to the HTTP design considered earlier — the heartbeat
  job already runs on the same event loop as all scheduled jobs, so its file going stale is
  exactly the "hung worker" signal from spec User Story 1. A file mtime check is what the
  Kubernetes docs' own heartbeat-file example uses (`stat -c %Y /tmp/heartbeat`), so the probe
  command in `contracts/worker-health-probes.md` follows that exact idiom.
- **Alternatives considered**: unchanged from the HTTP-based draft — a dedicated probe job or a
  raw `scheduler.running` check were both rejected for the same reasons (duplication /
  insufficient to prove forward progress).

## 3. What should the readiness signal be based on?

- **Decision**: A `ready` file, present only while the worker is (a) past startup and (b) the
  database was reachable as of the most recent periodic check. A background asyncio task
  (`_readiness_poller`, symmetric to the existing `_jobstore_poller`) runs every
  `worker_readiness_check_interval_seconds` (default 15s), calls the shared
  `check_database(readiness_db_timeout_seconds)` helper (see §4), and touches or removes the
  `ready` file accordingly. On SIGTERM/SIGINT, the file is removed immediately, before the
  scheduler shuts down, so readiness flips to not-ready as soon as termination begins (spec Edge
  Cases: graceful shutdown). The `readinessProbe` `exec` command is a plain `test -f`.
- **Rationale**: Matches spec FR-003/FR-004/FR-005: absent during startup (file doesn't exist
  until the poller's first successful check after startup completes), present once initialized
  and DB-reachable, removed if DB connectivity is lost or shutdown begins. A background poller
  is necessary here (unlike an `httpGet` probe, which could check on-demand) because an `exec`
  probe can only test *current* file state, not run an async DB query itself.
- **Alternatives considered**:
  - *Have the `readinessProbe` exec command itself attempt a DB connection* (e.g., via `psql` or
    a Python one-liner spawned per probe tick): rejected — forking a process and opening a fresh
    DB connection on every probe tick is exactly the "exec probes are more expensive" cost the
    Kubernetes docs warn about, and duplicates connection/timeout handling outside the
    already-tested `check_database` helper.
  - *Deriving readiness from the same `heartbeat` file as liveness*: rejected — conflates two
    different failure modes (hung loop vs. unreachable dependency), violating FR-007's
    requirement that DB outages must not affect liveness.

## 4. How should the DB-connectivity check be shared between `api` and `worker`?

- **Decision**: Extract the existing `_check_db` logic from `src/api/v1/endpoints/health.py`
  into `src/lib/services/health_check.py` as a public `check_database(timeout: float) -> bool`
  coroutine; both `src/api/v1/endpoints/health.py` and the new `src/worker/health.py` readiness
  poller import it from `lib/`.
- **Rationale**: Constitution's layer rule: `lib/` must not import from `api/` or `worker/`, but
  both may import from `lib/`. This is the only direction consistent with that rule while
  avoiding duplicated DB-check code (DRY) — unchanged from the original analysis.
- **Alternatives considered**: duplicating the check in `src/worker/health.py` — rejected, DRY
  violation and risk of drift (e.g., differing timeout handling).

## 5. How should the startup probe be implemented?

- **Decision**: A `started` file, touched exactly once after the scheduler has started and the
  heartbeat job has been registered. `startupProbe` uses `exec: test -f /tmp/started` with a
  generous `failureThreshold` × `periodSeconds` (e.g., 30 × 10s = up to 5 minutes), matching the
  Kubernetes docs' own startup-probe example. While the startup probe has not yet succeeded,
  Kubernetes suppresses liveness/readiness checks entirely, so a slow-starting worker (loading
  many persisted schedules) cannot be killed prematurely (spec User Story 3).
- **Rationale**: Simple, matches the reference pattern exactly, requires no extra logic beyond
  "touch a file once startup work is done."
- **Alternatives considered**: reusing the `heartbeat` file as the startup signal too — rejected;
  keeping them separate makes each file's meaning unambiguous and matches the reference's
  three-separate-files pattern.

## 6. Where do the actual Kubernetes manifests/Helm values live, and what does this feature deliver there?

- **Decision**: Unchanged — this feature delivers the worker-side file-marker logic plus
  documented, copy-pasteable recommended `exec` probe YAML in
  `documentation/deployment/deployment.md` and `contracts/worker-health-probes.md`. Actual Helm
  chart/manifest edits happen in the deployment repository, outside this repository's scope.
- **Rationale**: Consistent with how the API's existing `/health`/`/ready` endpoints are already
  documented without any in-repo Kubernetes manifest, since none exists here.

## 7a. The heartbeat job must not be scheduled through the DB-backed jobstore

- **Finding (discovered during end-to-end quickstart validation against a real Postgres
  instance)**: The original design registered the `worker_heartbeat` job in the same
  `SQLAlchemyJobStore` ("default") used for user schedules. Under a real database outage,
  APScheduler's internal due-jobs polling against that jobstore starts failing
  ("Error getting due jobs from job store 'default'"), which stops **all** jobs registered in
  it — including the heartbeat — from ever being marked due. This meant `heartbeat` marker
  staleness (and therefore liveness) ended up coupled to database availability, directly
  violating FR-007 ("a database outage MUST NOT, by itself, cause the liveness check to fail").
- **Decision**: Register the heartbeat job in a separate in-memory `MemoryJobStore` (jobstore
  alias `"heartbeat"`), distinct from the DB-backed `"default"` jobstore used for user
  schedules. `scheduler.add_job(..., jobstore="heartbeat")`.
- **Rationale**: `MemoryJobStore` has no external dependency, so a database outage cannot affect
  whether APScheduler considers the heartbeat job due. This was verified live: stopping the
  Postgres container left `heartbeat` ticking on schedule (liveness stayed healthy) while the
  `ready` marker was correctly removed by the readiness poller shortly after (readiness failed
  as expected) — restoring the database brought `ready` back automatically. As a side effect,
  the pg8000/GCP-IAM stale-row workaround previously needed for the heartbeat job in the DB
  jobstore (see `173c80d "Fix worker_heartbeat insert for GCP"`) is no longer needed for the
  heartbeat job, since `MemoryJobStore` doesn't persist across restarts and has no stale-row
  failure mode.
- **Alternatives considered**:
  - *Leave the heartbeat job in the DB jobstore and accept the coupling*: rejected — this is
    exactly the failure mode FR-007 exists to prevent; a DB outage is a very plausible real-world
    event this feature must survive without also declaring the worker "hung."
  - *Have the liveness check bypass APScheduler and touch the heartbeat file from a separate,
    DB-independent timer instead of the job callback*: rejected as unnecessarily complex — moving
    the existing job to an in-memory jobstore is a one-line, well-supported APScheduler feature
    (multiple jobstores per scheduler) that keeps the heartbeat's existing code path intact.

## 7. Does the Alpine-based worker image support `exec` probes out of the box?

- **Decision**: Yes. The worker's `Dockerfile` (shared with `api`/`migrations`) is based on
  `python:3.14-alpine`, which includes a POSIX shell (`/bin/sh`, BusyBox `ash`) and BusyBox's
  `stat`, `test`, and `date` — all commands used in the recommended probe YAML. No additional
  packages need to be installed in the image.
- **Rationale**: The Kubernetes docs note that distroless/scratch images often lack a shell,
  which would force shipping a dedicated health-check binary; this does not apply here, since the
  existing Alpine base already has everything the recommended `exec` commands need.

# Deployment Instructions

This document provides step-by-step instructions for deploying DIAL Scheduler to a new environment.
DIAL Scheduler includes three services built from a single Docker image, selected by the `SERVICE` environment variable:

- `migrations` — applies database schema migrations and exits
- `api` — schedule CRUD and run listing/details
- `worker` — executes scheduled jobs

## Architecture

![DIAL Scheduler Architecture](DIAL%20Scheduler%20Diagram.drawio.png)

## Dependencies

DIAL Scheduler requires a PostgreSQL database as persistent storage.

## Deployment Steps

### 0. Create app api key in DIAL
Scheduler app will use api key attributed to its application for getting user credentials for current schedule execution.
Open DIAL Admin -> Access Management -> Keys -> Create:
- ID: scheduler_api_key
- Display Name: Scheduler Api Key
- Project: Dial Scheduler
- Generate Value

On key details page:
Open Roles Tab, add `scheduler` role (role with access to `dial_scheduler` app).

### 1. Build the image

Build the Docker image from [Dockerfile](/Dockerfile).

### 2. Migrations

- Set required environment variables from [Deployment Env Setup — Migrations](/README.md#deployment-env-setup--migrations).
- Deploy the image with `SERVICE=migrations`.
- The container runs `alembic upgrade head` and exits with code `0` on success.
- **Verification:** container exits `0`; logs show:

  ![Migrations Start Logs](Migrations%20Logs.png)

### 3. Api

- Set required environment variables from [Deployment Env Setup — Api](/README.md#deployment-env-setup--api).
- Deploy the image with `SERVICE=api`.
- **Verification:** logs show the application started:

  ![Api Start Logs](API%20Logs.png)

- Health and readiness probes are available:
  - `GET /health` — liveness check, returns `{"status":"ok"}`
  - `GET /ready` — readiness check, returns `{"status":"ready","database":"ok"}` or `503` when the DB is unreachable

### 4. Worker

- Set required environment variables from [Deployment Env Setup — Worker](/README.md#deployment-env-setup--worker).
- Deploy the image with `SERVICE=worker`.
- **Verification:** logs show the scheduler started:

  ![Worker Start Logs](Worker%20Logs.png)

- The worker exposes no HTTP/TCP listener; Kubernetes health is observed via three filesystem
  marker files under `WORKER_HEALTH_DIR` (default `/tmp`), checked with `exec` probes:
  - `started` — created once the scheduler has started and the heartbeat job is registered
  - `heartbeat` — updated on every heartbeat job tick; stale beyond `WORKER_LIVENESS_STALENESS_SECONDS` (default `180s`) means the worker is hung
  - `ready` — present only while the worker has started and the database was reachable as of the most recent check, run every `WORKER_READINESS_CHECK_INTERVAL_SECONDS` (default `15s`)

  Configure the following probes on the worker container in its Kubernetes Deployment/Helm
  chart (adjust the `180` in the liveness command if `WORKER_LIVENESS_STALENESS_SECONDS` is
  overridden, and the paths if `WORKER_HEALTH_DIR` is overridden):

  ```yaml
    updateStrategy:
      type: Recreate

    customLivenessProbe:
      exec:
        command:
          - /bin/sh
          - -c
          - '[ -f /tmp/heartbeat ] && [ $(( $(date +%s) - $(stat -c %Y /tmp/heartbeat) )) -lt 180 ]'
      periodSeconds: 30
      timeoutSeconds: 5
      failureThreshold: 3

    customReadinessProbe:
      exec:
        command: ["/bin/sh", "-c", "test -f /tmp/ready"]
      initialDelaySeconds: 15
      periodSeconds: 15
      timeoutSeconds: 5
      failureThreshold: 2

    customStartupProbe:
      exec:
        command: ["/bin/sh", "-c", "test -f /tmp/started"]
      periodSeconds: 10
      timeoutSeconds: 5
      failureThreshold: 30
  ```

  These commands rely on a POSIX shell and BusyBox `stat`/`test`/`date`, already present in the
  worker's Alpine-based image — no extra packages are needed.

> **Important — run exactly one worker replica.**
> APScheduler's PostgreSQL jobstore does not provide leader election. Running multiple workers
> against the same database causes duplicate job execution.

> **Important — heartbeat timing constraint.**
> `NOOP_INTERVAL_SECONDS` (default `60`) must be less than `WORKER_LIVENESS_STALENESS_SECONDS`
> (default `180`). If `NOOP_INTERVAL_SECONDS` is set equal to or greater than
> `WORKER_LIVENESS_STALENESS_SECONDS`, the heartbeat file will always appear stale and the
> liveness probe will continuously restart the worker.

## Configure the App in DIAL Admin

### App Definition

Copy [scheduler-app-definition.json](scheduler_app/scheduler-app-definition.json)

Populate values:

- <SCHEDULER_URL> (2 places)- scheduler api url in k8s cluster, default http://dial-scheduler-api.dial-scheduler
- <SHA256(SCHEDULER_API_KEY)> - sha256 hash of scheduler api-key form ### 0. Create app api key in DIAL

### Create Assets App
1. Open DIAL Admin, Assets-> Applications (<Admin URL>/en/assets-applications)
2. Create -> Import
3. Select populated scheduler-app-definition.json
4. Open application details -> External Services -> DIAL Native -> Grant consent

### Per-user credentials

Each schedule executes using the credentials of the user who created it, not a shared service
key: `POST /schedules/` requires a `service_id` naming one of the app's registered
`external_services` (e.g. `dial-api-key` above). Before a schedule fires, the worker fetches
on-behalf-of credentials for the schedule's owner and `service_id` from DIAL; if that owner has
not signed in to the external service, the run fails with a credentials-stage error and the
target action is never dispatched (see `documentation/api_contract.md` and
`documentation/scheduled_job_execution.md`). Users sign in to an external service through DIAL's
standard external-service sign-in flow before creating schedules that depend on it.

For DIAL chat scheduler there is pre-created external service with id `dial-native`

### API Access

Once deployed, the scheduler API is accessible through DIAL at:

```
<DIAL_URL>/v1/deployments/applications/public/dial_scheduler__1.0.0/route/v1/schedules/
```

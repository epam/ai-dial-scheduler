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
  startupProbe:
    exec:
      command: ["/bin/sh", "-c", "test -f /tmp/started"]
    periodSeconds: 10
    failureThreshold: 30   # up to 5 minutes to complete startup before being killed

  livenessProbe:
    exec:
      command:
        - /bin/sh
        - -c
        - '[ $(( $(date +%s) - $(stat -c %Y /tmp/heartbeat) )) -lt 180 ]'
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3

  readinessProbe:
    exec:
      command: ["/bin/sh", "-c", "test -f /tmp/ready"]
    periodSeconds: 15
    timeoutSeconds: 5
    failureThreshold: 2
  ```

  These commands rely on a POSIX shell and BusyBox `stat`/`test`/`date`, already present in the
  worker's Alpine-based image — no extra packages are needed.

## Configure the App in DIAL Admin

### App Definition

```json
{
  "name": "dial_scheduler",
  "display_name": "DIAL Scheduler",
  "description": "",
  "forward_auth_token": false,
  "features": {
    "system_prompt_supported": true,
    "tools_supported": false,
    "seed_supported": false,
    "url_attachments_supported": false,
    "folder_attachments_supported": false,
    "allow_resume": true,
    "accessible_by_per_request_key": true,
    "content_parts_supported": false,
    "temperature_supported": true,
    "parallel_tool_calls_supported": true,
    "assistant_attachments_in_request_supported": false
  },
  "defaults": {},
  "responses_defaults": {},
  "interceptors": [],
  "description_keywords": [],
  "max_retry_attempts": 1,
  "dependencies": [],
  "application_properties": {},
  "mcp": {
    "endpoint": "http://dial-scheduler-api.dial-scheduler/mcp",
    "transport": "http",
    "allowedTools": [],
    "configDelivery": "meta",
    "forwardPerRequestKey": true
  },
  "routes": {
    "main": {
      "name": "main",
      "rewritePath": true,
      "paths": [
        "/.*"
      ],
      "methods": [
        "DELETE",
        "GET",
        "PATCH",
        "POST",
        "PUT"
      ],
      "upstreams": [
        {
          "endpoint": "http://dial-scheduler-api.dial-scheduler",
          "weight": 1,
          "tier": 0
        }
      ],
      "maxRetryAttempts": 1,
      "order": 2147483647,
      "permissions": [
        "READ",
        "WRITE"
      ],
      "attachmentPaths": {
        "requestBody": [],
        "responseBody": []
      }
    }
  }
}
```

### Steps

1. Open DIAL Admin.
2. Create the application:
   - **ID:** `dial_scheduler`
   - **Display Name:** `DIAL Scheduler`
   - **Chat Endpoint:** `false`
   - **MCP Endpoint:** `true`, Endpoint: `http://dial-scheduler-api.dial-scheduler/mcp`
   - Click **Create**.
3. Open the created application → **JSON Editor → Core Format**.
4. Paste the App Definition JSON above and save.

### API Access

Once deployed, the scheduler API is accessible through DIAL at:

```
{DIAL_URL}/v1/deployments/dial_scheduler/route/v1/schedules/
```

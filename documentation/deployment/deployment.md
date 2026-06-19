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

# Changelog

This file covers releases up to `0.3.1`. Release notes for `0.4.0` and later are published as
[GitHub Releases](https://github.com/epam/ai-dial-scheduler/releases), generated from the commit history.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.3.1] - 2026-10-19

### Added
- `idle_timeout` schedule property: the maximum gap in seconds between two consecutive stream
  events. It resets on every event and applies only to streaming targets (`sse`, or
  `chat_completion`/`responses` with `payload.stream: true`). It counts parsed events, so SSE
  heartbeat pings do not reset it. `idle_timeout > timeout` is rejected when both are set
  (`src/lib/schemas/schedule.py`, `src/worker/executors/base.py`).
- Per-process timeout defaults, each available as `API_HTTP_*` and `WORKER_HTTP_*`:
  `*_MAX_DURATION_SECONDS` (600, ceiling for a non-streaming target),
  `*_STREAMING_MAX_DURATION_SECONDS` (3600, ceiling for a streaming target) and
  `*_IDLE_TIMEOUT_SECONDS` (60) (`src/lib/settings.py`).
- `attempt_idle_timed_out` log event, distinct from `attempt_timed_out`, so an idle expiry and a
  ceiling expiry can be told apart without reading the message (`src/worker/retry.py`).

### Changed
- A run's `timeout` is now an absolute backstop. When the schedule omits it, the default is 3600 s
  for a streaming target and 600 s otherwise, instead of the single 600 s `*_HTTP_TIMEOUT_SECONDS`.
  Healthy long-running streams are no longer cut off at a fixed bound.
- Executors keep the client's configured connect, write and pool timeouts and override only the
  read phase, so an unreachable host fails within `*_HTTP_CONNECT_TIMEOUT_SECONDS` (5 s) instead
  of at the run ceiling (`src/worker/executors/`).
- DIAL metadata calls (user info, OBO credentials, conversation writes) use the shared client's
  per-phase timeouts instead of a flat 600 s on every phase (`src/lib/clients/dial_client.py`).

### Removed
- `API_HTTP_TIMEOUT_SECONDS` and `WORKER_HTTP_TIMEOUT_SECONDS`. They no longer bound runs. A
  deployment that still sets them starts normally and the value is ignored.

## [0.3.0] - 2026-10-07

### Added
- AWS RDS / Aurora PostgreSQL IAM database authentication: set `DB_AUTH_MODE=aws_iam` with
  `DB_AWS_IAM_USER` (and optionally `DB_AWS_REGION`) instead of `DB_PASSWORD` / `DB_URL`. Tokens are
  generated via `boto3` for both the async engine and Alembic migrations (`src/lib/db/credentials.py`,
  `src/lib/db/engine.py`, `src/lib/db/sync_engine.py`).
- `POST /v1/schedules/{schedule_id}/run` — run a schedule once on demand. Returns `202` with an
  `in_progress` run and executes it in the background **inside the API service**, using only the
  stored definition; the schedule's `next_run_time` is unaffected (`src/api/v1/endpoints/schedules.py`,
  `src/lib/services/run_launcher.py`, `ScheduleRunService.create_on_demand_run`). Exposed over MCP as
  `run_schedule_now`.
- `model` on schedule list rows, plus a `model` query parameter on `GET /v1/schedules/` matching it
  as a case-insensitive substring. It is `null` for payloads without a model (REST/SSE targets), and
  such schedules never match the filter. Backed by a generated `schedules.model` column (migration
  `b7c5e6f8a9d0`) so listing and filtering never detoast `kwargs`: on 50k schedules with long chat
  histories a filtered list runs in ~25 ms instead of ~195 ms.

### Changed
- `RunContext` now carries the HTTP client and default timeout a run executes with, so the same
  execution core (`src.worker.jobs.execute_run`) serves both the worker and the API. The worker
  keeps its shared client and `WORKER_HTTP_*` timeouts; the API uses its app-state client and
  `API_HTTP_*` timeouts (`src/lib/schemas/run.py`, `src/worker/`).
- Bumped `anyio` to `>=4.14.2` (CVE-2026-63374).
- Bumped `pyjwt` to `>=2.14.0` (CVE-2026-102266, -102267, -102268, -102271, -102272, -102273) and
  `urllib3` to `>=2.8.0` (CVE-2026-97687, CVE-2026-97689); locked at 2.15.1 and 2.8.0.

## [0.2.0] - 2026-09-21

### Added
- Sign-in status endpoint and MCP notifications; schedule create/update now enforce a sign-in precondition (`src/api/v1/endpoints/sign_in.py`, `src/api/dependencies/sign_in.py`, `src/api/mcp/`).
- Persisted DIAL conversation id on schedule runs, exposed via the run detail/list API (`src/lib/models/schedule_run.py`, `src/lib/schemas/run.py`, migration `a1b2c3d4e5f6`).
- Open-source preparation docs: `CODESTYLE.md`, `CONFIGURATION.md`, `CONTRIBUTING.md`, `SECURITY.md`.

### Changed
- Switched e2e tests and local setup to use DIAL native auth/services instead of mocks (`dial/docker-compose.yml`, `dial/keycloak/import/realm-config.json`, `tests/e2e/*.http`).
- Updated deployment scheduler app definition and refreshed `uv.lock` for the UAT release.
- Updated `README.md` and `documentation/api_contract.md`/`documentation/db_tables.md` to reflect the sign-in and conversation-id changes.

## [0.1.1] - 2026-09-21
Released from `release-0.1` with the same changes as [0.2.0](#020---2026-09-21).

## [0.1.0] - 2026-08-14
Baseline release used as the starting point for this changelog.

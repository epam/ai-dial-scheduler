# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- `POST /v1/schedules/{schedule_id}/run` — run a schedule once on demand. Returns `202` with an
  `in_progress` run and executes it in the background **inside the API service**, using only the
  stored definition; the schedule's `next_run_time` is unaffected (`src/api/v1/endpoints/schedules.py`,
  `src/lib/services/run_launcher.py`, `ScheduleRunService.create_on_demand_run`). Exposed over MCP as
  `run_schedule_now`.

### Changed
- `RunContext` now carries the HTTP client and default timeout a run executes with, so the same
  execution core (`src.worker.jobs.execute_run`) serves both the worker and the API. The worker
  keeps its shared client and `WORKER_HTTP_*` timeouts; the API uses its app-state client and
  `API_HTTP_*` timeouts (`src/lib/schemas/run.py`, `src/worker/`).

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

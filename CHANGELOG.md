# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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

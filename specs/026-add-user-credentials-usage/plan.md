# Implementation Plan: Execute Schedules with Per-User Credentials

**Branch**: `026-add-user-credentials-usage` | **Date**: 2026-07-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/026-add-user-credentials-usage/spec.md`

## Summary

Replace the single shared `dial_api_key` used for every schedule execution with per-user
on-behalf-of (OBO) credentials fetched from DIAL at the start of each run. A schedule gains a
required `service_id` identifying the external service; the worker resolves the schedule's owner
and service, calls DIAL's `POST /v1/ops/external-service/obo-credentials` endpoint, and applies
the returned `header_name`/`header_value` to the outgoing target request across all three
executors (REST, SSE, chat completion). If credentials cannot be obtained, the run is recorded as
an error whose result captures the credentials-lookup response and is distinguishable from a
target-request error. A data migration backfills existing schedules with `service_id = "dial-oauth"`.

Technical approach: extend `DialClient` (in `src/lib`, already shared) with an OBO method used by
the worker; propagate credentials through `RunContext`; fetch schedule owner/service from the
`schedules` table in the worker (authoritative for pre-existing APScheduler jobs whose stored
kwargs predate this feature); update settings, schemas, model, endpoints, and MCP tool guidance.

## Technical Context

**Language/Version**: Python 3.14

**Primary Dependencies**: FastAPI, APScheduler, SQLAlchemy/SQLModel, Pydantic v2, httpx, openai (Azure), Alembic, FastMCP

**Storage**: PostgreSQL (schedules, schedule_runs, apscheduler_jobs)

**Testing**: pytest + pytest-asyncio (`asyncio_mode = "auto"`), pytest-mock

**Target Platform**: Linux server (API service + APScheduler worker service, both containerized)

**Project Type**: Web service + background worker (`src/api`, `src/worker`, shared `src/lib`)

**Performance Goals**: One additional DIAL OBO call per schedule execution (no caching, per FR-011); negligible against existing per-run HTTP work.

**Constraints**: No secrets logged or persisted (OBO `header_value` is a secret — must never be logged and must not be stored in run results); async I/O throughout; `mypy`, `ruff` gates must pass.

**Scale/Scope**: Existing schedule volume; change touches settings, one model + migration, request/response schemas, three executors, worker job orchestration, `DialClient`, and MCP tool guidance.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Reliability First**: PASS. Credential-fetch failure is handled explicitly (run marked error, no target request sent). Fetch is a single awaited async call reusing the worker's shared `httpx.AsyncClient`; transport/HTTP errors are caught and mapped to a recorded run error. Credential failure is terminal and deliberately bypasses the target-request retry policy (retries apply to the target only).
- **II. Observability (NON-NEGOTIABLE)**: PASS. New structured log events (`credentials_fetch_start`, `credentials_fetch_succeeded`, `credentials_fetch_failed`) via `src.lib.utils.logging` `logger`, keyed by `schedule_id`/`run_id`/`service_id`/`owner_sub`. The secret `header_value` is NEVER logged. All runs (success + credential failure) persist to `schedule_runs`.
- **III. Test-First (NON-NEGOTIABLE)**: PASS (enforced in `/speckit-implement`). Unit tests: OBO request construction, success/failure mapping, header replacement in each executor, owner/service resolution, settings defaults, migration backfill. Integration test: end-to-end run using a stubbed DIAL OBO endpoint.
- **IV. Contract-Driven API**: PASS. `service_id` added to the versioned request/response schemas (`ScheduleRequest`/`ScheduleResponse`) → auto-published via OpenAPI (also surfaced through MCP). New internal contracts are explicit Pydantic models (`OboCredentialsRequest`, `OboCredentials`) — no ad-hoc dicts across the worker→DIAL boundary. `RunContext` extended with typed credential fields.
- **V. Simplicity & YAGNI**: PASS. No caching layer (per decision). Reuse existing `DialClient`, `HttpClientProvider`, `RunContext`, and `ScheduleRunRepository`. New parameters grouped in Pydantic models to respect the 2–3 parameter rule.

**Result: PASS — no complexity deviations to track.**

## Project Structure

### Documentation (this feature)

```text
specs/026-add-user-credentials-usage/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── obo-credentials.md      # DIAL OBO endpoint contract (consumed)
│   └── schedule-api.md         # service_id additions to schedule create/update/response
└── checklists/
    └── requirements.md  # Spec quality checklist (already created)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── mcp/
│   │   └── server.py                    # (unchanged) MCP generated from OpenAPI
│   └── v1/endpoints/
│       └── schedules.py                 # MODIFY: examples + docstring guidance for service_id / dial-oauth default
├── worker/
│   ├── jobs.py                          # MODIFY: resolve owner+service, fetch OBO creds, inject into RunContext
│   └── executors/
│       ├── rest.py                      # MODIFY: use OBO header instead of shared Api-Key
│       ├── sse.py                       # MODIFY: use OBO header instead of shared Api-Key
│       └── chat_completion.py           # MODIFY: use OBO header instead of shared Api-Key
└── lib/
    ├── settings.py                      # MODIFY: scheduler_app_id, user_id_path_list default
    ├── models/schedule.py               # MODIFY: add service_id column
    ├── schemas/
    │   ├── schedule.py                  # MODIFY: service_id on request/response/list item
    │   ├── run.py                       # MODIFY: RunContext auth header fields
    │   └── credentials.py               # NEW: OboCredentialsRequest, OboCredentials, RunError
    ├── clients/dial_client.py           # MODIFY: add get_obo_credentials()
    ├── services/schedule_service.py     # MODIFY: persist service_id on create/update, map to response
    ├── repositories/
    │   ├── schedule_repository.py       # MODIFY: add get_by_id() (owner-agnostic) for the worker
    │   └── schedule_run_repository.py   # MODIFY: structured credential-error result (distinguishable stage)
    └── constants.py                     # MODIFY: OBO path + service url template (no "default service id" constant — see below)

migrations/versions/
└── <ts>_add_service_id_to_schedules.py  # NEW: add column, backfill existing rows with 'dial-oauth', set NOT NULL

tests/                                   # Mirrors src/ — unit + integration coverage per Constitution III
```

**Structure Decision**: Existing `src/api` + `src/worker` + shared `src/lib` layout (constitution source of truth). `DialClient` lives in `src/lib/clients`, so the worker reuses it for the OBO call. The dependency arrow stays inward-only (`worker/` and `api/` → `lib/`).

## Key Design Decisions

1. **Owner + service resolution in the worker comes from the `schedules` table, not APScheduler job kwargs.** The migration backfills the DB column, but cannot rewrite kwargs of already-registered APScheduler jobs. Reading `service_id`/`created_by` from the schedules row via a new `ScheduleRepository.get_by_id(schedule_id)` is authoritative for both new and pre-existing jobs. See [research.md](./research.md) §1.

2. **`service_id` is a top-level `Schedule` column** (sibling of `created_by`), not part of `ScheduleProperties`/`kwargs` — it identifies the credential source, not a target-call property.

3. **`owner_sub` = the schedule's stored `created_by`.** Unattended runs have no incoming token to re-resolve; `created_by` was resolved at create time. `user_id_path_list` becomes `["sub", "project"]` so new schedules store the subject. Legacy schedules (email-based `created_by`) are best-effort and fail per FR-009 if DIAL cannot resolve them. See [research.md](./research.md) §2.

4. **Credential failure is terminal and pre-empts the retry policy.** The OBO call happens in `jobs.py` before `execute_with_policy`, so the target-request retry budget is untouched (spec edge case).

5. **Header application is generic** (`header_name`/`header_value`); the shared `dial_api_key` is removed from all three executors. `header_value` is carried as `SecretStr` in `RunContext` and never logged/persisted. Chat-completion nuance (AsyncAzureOpenAI) documented in [research.md](./research.md) §3.

6. **Both external-service auth types are covered.** `dial/core/config-template.json` registers `dial-api-key` (API_KEY / `Api-Key` header) and `dial-oauth` (OAUTH / `Authorization: Bearer`). The generic header path handles both; unit tests parametrize both shapes. See [research.md](./research.md) §8.

7. **Real-DIAL integration uses `scripts/dial_sign_in.py`** to sign a user in to the `dial-oauth` external service so the OBO fetch resolves; the script prints the `sub` that must match the schedule's `created_by`. Documented in the README and referenced from the opt-in integration test. See [research.md](./research.md) §9.

8. **Post-implementation documentation deliverables** (Polish phase): update `README.md`, add `service_id` to every create/update body in `tests/helpers/verify.http`, and document the sign-in helper / prerequisite.

## Complexity Tracking

> No Constitution Check violations — table intentionally empty.

# Tasks: AWS RDS IAM Database Authentication

**Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)

## Format: `[ID] [P?] [Story] Description`

- `[P]` — may run in parallel with other `[P]` tasks (different files, no ordering dependency).
- `[Story]` — the user story from spec.md the task serves (US1/US2/US3), or `FOUND` for shared
  foundations and `POLISH` for cross-cutting work.

## Path Conventions

Source under `src/`, tests mirroring it under `tests/unit/lib/...`.

## Phase 1: Setup

- [x] T001 [FOUND] Add `boto3>=1.40,<2` to `[project].dependencies` in `pyproject.toml` and refresh
      `uv.lock`.

## Phase 2: Foundational (blocking prerequisites)

These are shared by every story: the mode cannot be selected, configured or validated without them.

- [x] T002 [FOUND] Add `AWS_IAM = "aws_iam"` to `DbAuthMode` in `src/lib/db/enums.py`.
- [x] T003 [FOUND] Add `db_aws_iam_user` and `db_aws_region` to `DatabaseSettings`
      (`src/lib/settings.py`) and register `aws_iam` in both `required_fields_by_mode`
      (`db_aws_iam_user`) and `forbidden_fields_by_mode` (`db_password`, `db_url`).
- [x] T004 [FOUND] Add `AwsRdsTokenProvider` and `AsyncAwsRdsTokenProvider` to
      `src/lib/db/credentials.py`: resolve the region (`db_aws_region` → `AWS_REGION` → botocore →
      `ValueError` naming `DB_AWS_REGION`), hold one RDS client, return a fresh token per call, close the client;
      the async variant delegates to `asyncio.to_thread`.

## Phase 3: User Story 1 — async engine on AWS IAM (P1) 🎯 MVP

### Tests first (confirm FAILING) ⚠️

- [x] T005 [P] [US1] `tests/unit/lib/test_settings_db_auth.py` — `aws_iam` requires
      `db_aws_iam_user`; rejects `db_password`/`db_url`; a valid config constructs; `db_aws_region`
      is optional.
- [x] T006 [P] [US1] `tests/unit/lib/test_db_credentials.py` — token requested with host/port/user/
      region; explicit `DB_AWS_REGION` wins over the environment; `AWS_REGION` and `AWS_DEFAULT_REGION`
      are both honoured; unresolvable region raises naming
      `DB_AWS_REGION`; `close()` closes the client; the async provider returns the same token off the
      event loop.
- [x] T007 [P] [US1] `tests/unit/lib/test_db_engine_selection.py` — `aws_iam` dispatches to
      `_init_engine_aws_iam`; `getconn` uses the fresh token as the password with `ssl="require"`;
      `search_path` applied when `DB_SCHEMA` is set and skipped when it is not; failures log
      mode + exception type and no token.

### Implementation

- [x] T008 [US1] Add `_init_engine_aws_iam` to `Database` (`src/lib/db/engine.py`) and dispatch
      `DbAuthMode.AWS_IAM` to it from `init_engine`.
- [x] T009 [US1] Hold the async provider on `Database._aws_credential`; release it in `dispose()`
      and in `_release_credentials_best_effort()` (sync `close()`, so no thread/timeout dance is
      needed).

## Phase 4: User Story 2 — sync job-store engine on AWS IAM (P1)

### Tests first (confirm FAILING) ⚠️

- [x] T010 [P] [US2] `tests/unit/lib/test_db_url.py` — `build_aws_iam_sync_url` yields a
      passwordless `postgresql+psycopg2://` URL carrying the IAM user, host and database; the
      existing Azure URL test still passes after the shared-helper refactor.
- [x] T011 [P] [US2] `tests/unit/lib/test_sync_engine.py` — `aws_iam` builds a passwordless
      psycopg2 engine with `sslmode=require`; the shared `_inject_token_password` sets a fresh
      password per connection; failures re-raise and log `mode=aws_iam` without the exception
      message; an unresolvable region fails at engine build; disposal closes the provider.
      (`DB_SCHEMA` needs no aws-specific test: the listener is attached in `build_sync_engine`
      after dispatch and is therefore mode-independent.)

### Implementation

- [x] T012 [US2] Factor the passwordless URL construction in `src/lib/db/url.py` into one helper and
      expose `build_aws_iam_sync_url` alongside the existing `build_azure_entra_sync_url`.
- [x] T013 [US2] Add `_build_aws_engine` and generalize the azure token injection into the shared `_inject_token_password` + `_attach_token_password_listeners` to `src/lib/db/sync_engine.py` and
      dispatch `DbAuthMode.AWS_IAM` from `build_sync_engine`.

## Phase 5: User Story 3 — region resolution (P2)

Covered by T004's resolution order and T006's assertions; no separate implementation task. Verify
the error text names `DB_AWS_REGION` so the operator knows the fix (SC-004).

- [x] T014 [US3] Confirm engine construction (not first connect) is where an unresolvable region
      fails, for both the async and the sync builder.

## Phase 6: Polish & Cross-Cutting

- [x] T015 [P] [POLISH] `README.md` — add `aws_iam` to the auth-mode table, add a dotenv example,
      and correct the `DB_AUTH_MODE` rows in the three per-service env tables (they still claim only
      `password`/`connection-string`, predating feature 022).
- [x] T016 [P] [POLISH] `.env.example` — commented `aws_iam` block.
- [x] T017 [POLISH] Quality gates: `uv run ruff check src tests`, `uv run ruff format src tests`,
      `uv run mypy src`, `uv run pytest`.
- [x] T018 [POLISH] `code-review-agent` pass; address findings and re-run until clean.

## Dependencies & Execution Order

### Phase dependencies

Phase 1 → Phase 2 → (Phase 3 ∥ Phase 4) → Phase 5 verification → Phase 6.

Phases 3 and 4 are independent once Phase 2 lands: they touch `engine.py` and
`sync_engine.py`/`url.py` respectively, sharing only the provider added in T004.

### Story dependencies

US1 and US2 are both P1 and jointly required for a usable deployment — the API needs the async
engine, the worker needs both. US3 is a property of the provider from T004 and lands with it.

### Shared-file coordination (not parallel)

- `src/lib/db/credentials.py` — T004 only.
- `src/lib/db/engine.py` — T008 then T009, same file, sequential.
- `src/lib/db/url.py` — T012 only (refactor + addition in one edit).

### Parallel opportunities

T005–T007 (three separate test files), T010–T011, and T015–T016.

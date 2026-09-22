# Implementation Plan: AWS RDS IAM Database Authentication

**Branch**: `038-db-aws-iam-auth`

**Spec**: [spec.md](./spec.md)

**Date**: 2026-09-16

## Summary

Add a fifth `DB_AUTH_MODE`, `aws_iam`, that authenticates to Amazon RDS / Aurora for PostgreSQL with
an IAM auth token generated from the workload's AWS credential chain. The token is used as the
connection password and regenerated for every new connection, on both the async (asyncpg) engine and
the sync (psycopg2) engine that backs the APScheduler job store. The implementation mirrors the
existing `azure_entra` path — same provider/creator/listener shape, same TLS posture — so the change
is additive and the four existing modes are untouched.

## Technical Context

- **Language / runtime**: Python 3.14, asyncio.
- **New dependency**: `boto3>=1.40,<2` (runtime). Brings `botocore`, `jmespath`, `s3transfer`.
- **Token API**: `boto3.client("rds").generate_db_auth_token(DBHostname, Port, DBUsername, Region)` —
  local SigV4 presign, 15-minute validity, no network call of its own.
- **Async path**: `create_async_engine("postgresql+asyncpg://", async_creator=getconn)` where
  `getconn` awaits the token and calls `asyncpg.connect(..., ssl="require")`, then applies
  `search_path` when `DB_SCHEMA` is set.
- **Sync path**: passwordless `postgresql+psycopg2://` URL, `connect_args={"sslmode": "require"}`, a
  `do_connect` listener injecting the token, and an `engine_disposed` listener closing the provider.
  `attach_schema_listener` handles `search_path` as it already does for every sync mode.
- **Blocking I/O**: token generation runs in `asyncio.to_thread` on the async path, because AWS
  credential resolution/refresh (IRSA/IMDS/STS) can perform I/O.
- **Alembic**: online migrations go through `Database.init_engine()` and therefore work unchanged;
  offline stays unsupported for non-URL modes, so `migrations/env.py` needs no edit.
- **Testing**: pytest + pytest-mock unit tests; boto3 is always mocked — no test contacts AWS.

## Constitution Check

| Principle | Compliance |
|---|---|
| I. Reliability First | Uses the AWS-supported token API; per-connection tokens avoid expiry-related outages; `to_thread` keeps the event loop free; TLS enforced explicitly rather than by driver default; provider lifecycle released on dispose and on `force_fresh` re-init. |
| II. Observability | Engine construction logs the mode (existing `init_engine` log covers the new branch; the sync builder logs `Building sync DB engine.` with `mode=aws_iam`). Credential failures log mode + exception type via `src.lib.utils.logging`, never token material. No `print()`. |
| III. Spec-first / TDD | This spec folder precedes implementation; unit tests are written per user story before the code. |
| IV. Conventions | ruff (line-length 120), full type hints, Google-style docstrings on the new public provider classes, `snake_case`, imports ordered by ruff/isort, no `assert` in `src/`. |
| V. Layering | All changes live under `src/lib/db/` and `src/lib/settings.py`. Nothing in `api/` or `worker/` changes — both consume the same `Database` singleton and `build_jobstore`, which is exactly why one change covers both services. |

No deviation requiring Complexity Tracking.

## Project Structure

### Documentation (this feature)

```
specs/038-db-aws-iam-auth/
├── spec.md
├── research.md
├── data-model.md
├── quickstart.md
├── plan.md
├── tasks.md
└── contracts/
    └── db-auth-config.md
```

### Source Code

```
pyproject.toml                     # + boto3 dependency
src/lib/db/enums.py                # + DbAuthMode.AWS_IAM
src/lib/settings.py                # + db_aws_iam_user, db_aws_region + validation entries
src/lib/db/credentials.py          # + AwsRdsTokenProvider, AsyncAwsRdsTokenProvider
src/lib/db/url.py                  # + build_aws_iam_sync_url (shared passwordless helper)
src/lib/db/engine.py               # + _init_engine_aws_iam, dispatch, credential release
src/lib/db/sync_engine.py          # + _build_aws_engine, shared _inject_token_password, dispatch
README.md                          # auth-mode table, example, per-service env tables
.env.example                       # commented aws_iam block
```

```
tests/unit/lib/test_settings_db_auth.py       # aws_iam validation
tests/unit/lib/test_db_credentials.py         # provider: token args, region, close, to_thread
tests/unit/lib/test_db_url.py                 # passwordless psycopg2 URL with the IAM user
tests/unit/lib/test_db_engine_selection.py    # async dispatch + getconn behaviour
tests/unit/lib/test_sync_engine.py            # sync builder, TLS, token injection, logging
```

No new files outside `src/lib/db/`; no migration; no API surface change.

## Complexity Tracking

Not applicable — no constitutional deviation. The only judgement call is the region fallback living
in the provider rather than in `DatabaseSettings` (research.md, Decision 4), because the fallback is
only knowable at runtime.

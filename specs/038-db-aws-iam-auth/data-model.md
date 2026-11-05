# Data Model: AWS RDS IAM Database Authentication

No persisted entities, tables, columns or migrations are introduced by this feature. It changes
configuration and connection construction only.

## Enum extension — `src/lib/db/enums.py`

`DbAuthMode` gains one member:

| Member | Value |
|--------|-------|
| `AWS_IAM` | `aws_iam` |

`DbDriver` is unchanged: the `aws_iam` sync path uses `postgresql+psycopg2`, the async path
`postgresql+asyncpg` (via an `async_creator`, so the URL carries no host).

## Settings extension — `src/lib/settings.py` (`DatabaseSettings`)

| Field | Env var | Type | Default | Notes |
|-------|---------|------|---------|-------|
| `db_aws_iam_user` | `DB_AWS_IAM_USER` | `str \| None` | `None` | Required in `aws_iam` mode: the PostgreSQL role granted `rds_iam`. |
| `db_aws_region` | `DB_AWS_REGION` | `str \| None` | `None` | Optional; falls back to `AWS_REGION`, then `AWS_DEFAULT_REGION`/shared config. |

Reused in `aws_iam` mode: `db_host`, `db_port`, `db_name`, `db_schema`, and all `db_pool_*` /
`db_echo` fields.

### Validation (`validate_auth_requirements`)

| Mode | Required | Must NOT be set |
|------|----------|-----------------|
| `aws_iam` | `db_aws_iam_user` | `db_password`, `db_url` |

Region is deliberately absent from both columns — see research.md, Decision 4.

## Runtime objects

- **`AwsRdsTokenProvider`** (`src/lib/db/credentials.py`) — holds one boto3 RDS client and the
  resolved region; `get_token_password()` returns a fresh 15-minute token; `close()` releases the
  client.
- **`AsyncAwsRdsTokenProvider`** — wraps the sync provider and awaits `get_token_password()` on a
  worker thread so credential-refresh I/O never blocks the event loop.
- **`Database._aws_credential`** — the async provider held by the engine singleton for the process
  lifetime, released by `dispose()` and by `_release_credentials_best_effort()`.

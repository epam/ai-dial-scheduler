# Configuration Contract: `aws_iam` Database Authentication Mode

This extends the contract in
`specs/022-db-workload-identity-auth/contracts/db-auth-config.md`, which stays authoritative for the
four pre-existing modes. All values are supplied as environment variables. No REST or message schema
changes.

## `DB_AUTH_MODE` (enum) — extended

| Value | Meaning |
|-------|---------|
| `password` *(default)* | Static username/password. |
| `connection-string` | Verbatim SQLAlchemy URL via `DB_URL`. |
| `gcp_iam` | Cloud SQL IAM auth via the workload's Google service account. |
| `azure_entra` | Microsoft Entra token auth via the workload's managed identity. |
| **`aws_iam`** *(new)* | **Amazon RDS / Aurora IAM auth via the workload's AWS credentials.** |

## `aws_iam` (new)

- **Required**: `DB_AWS_IAM_USER` — the PostgreSQL role that has been granted `rds_iam`
  (`GRANT rds_iam TO <role>`).
- **Uses**: `DB_HOST` (the RDS/Aurora endpoint FQDN), `DB_PORT` (default `5432`), `DB_NAME`
  (default `app`), plus the shared `DB_SCHEMA` and `DB_POOL_*` settings.
- **Optional**: `DB_AWS_REGION` — the region used to sign the token. When unset it falls back to
  `AWS_REGION`, then to botocore's own resolution (`AWS_DEFAULT_REGION`, shared config file).
  Instance metadata is not consulted. Engine construction fails with an error naming
  `DB_AWS_REGION` if no region can be resolved.
- **Must NOT be set**: `DB_PASSWORD`, `DB_URL`.
- **Workload requirement**: the workload's AWS identity (IRSA service account on EKS, task role on
  ECS, instance profile on EC2) must hold `rds-db:connect` on
  `arn:aws:rds-db:<region>:<account>:dbuser:<db-resource-id>/<DB_AWS_IAM_USER>`, the RDS instance
  must have IAM database authentication enabled, and the role must exist in PostgreSQL with
  `rds_iam` granted.
- **Credentials**: resolved through the standard AWS credential chain. Static access keys are not a
  supported configuration surface of this service — they are neither read nor documented here — but
  the chain will pick up whatever the platform provides.

## Contract guarantees

1. **Startup validation**: A missing `DB_AWS_IAM_USER`, or a co-configured `DB_PASSWORD`/`DB_URL`,
   fails startup with a `ValueError` naming every offending variable. (FR-005, SC-003)
2. **Default**: Unset `DB_AUTH_MODE` still resolves to `password`. (FR-007)
3. **Uniformity**: `aws_iam` applies to the async engine, the APScheduler job store, and online
   Alembic migrations alike. (FR-008)
4. **TLS**: Connections are always encrypted (`require`), because RDS rejects IAM auth in the clear.
   The server certificate is not verified — see spec Out of Scope. (FR-004)
5. **Secret safety**: The token is never logged; credential failures log only the mode and the
   exception type. (FR-009, SC-005)
6. **Auto-refresh**: A fresh token is generated for every new connection, so the 15-minute token
   lifetime never requires a restart and is independent of `DB_POOL_RECYCLE_SECONDS`. (FR-003)
7. **Offline migrations**: `alembic` offline mode remains unsupported for `aws_iam`, as for the other
   workload-identity modes; it fails with the existing explanatory `RuntimeError`.

## Example configuration

```bash
# aws_iam
DB_AUTH_MODE=aws_iam
DB_HOST=scheduler-pg.abcdefghijkl.eu-central-1.rds.amazonaws.com
DB_PORT=5432
DB_NAME=scheduler
DB_AWS_IAM_USER=scheduler_iam
# DB_AWS_REGION=eu-central-1   # optional; resolved from the environment when omitted
```

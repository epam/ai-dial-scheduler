# Configuration Contract: Database Authentication Modes

This is the configuration contract for selecting how the API service, worker, and Alembic
authenticate to PostgreSQL. It is the only external interface this feature exposes (no REST/message
schema changes). All values are supplied as environment variables.

## `DB_AUTH_MODE` (enum)

| Value | Meaning |
|-------|---------|
| `password` *(default)* | Static username/password (existing behavior). |
| `connection-string` | Verbatim SQLAlchemy URL via `DB_URL` (existing behavior). |
| `gcp_iam` | Cloud SQL IAM database authentication via workload's Google service account. |
| `azure_entra` | Microsoft Entra ID token auth via workload's managed identity. |

## Required / optional variables per mode

### `password` (unchanged)
- **Required**: `DB_PASSWORD`
- Uses: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`

### `connection-string` (unchanged)
- **Required**: `DB_URL`

### `gcp_iam` (new)
- **Required**: `DB_GCP_INSTANCE_CONNECTION_NAME` (e.g. `project:region:instance`),
  `DB_GCP_IAM_USER` (IAM DB user, e.g. `svc@project.iam`)
- **Optional**: `DB_GCP_PRIVATE_IP` (`true`/`false`, default `false`), `DB_NAME`
- **Must NOT be set**: `DB_PASSWORD`
- Workload requirement: the runtime service account has the **Cloud SQL Client** role and a mapped
  IAM database user with privileges on `DB_NAME`.

### `azure_entra` (new)
- **Required**: `DB_AZURE_ENTRA_USER` (the Entra principal mapped as the DB role)
- Uses: `DB_HOST` (Azure PostgreSQL FQDN, default `localhost`), `DB_NAME` (default `app`),
  `DB_PORT` (default `5432`)
- **Optional**: `DB_AZURE_MANAGED_IDENTITY_CLIENT_ID` (for a user-assigned managed identity)
- **Must NOT be set**: `DB_PASSWORD`
- Workload requirement: a managed identity assigned to the workload and granted Entra access to the
  database; token scope used is `https://ossrdbms-aad.database.windows.net/.default`.

## Contract guarantees

1. **Startup validation**: If any required variable for the selected mode is missing, startup fails
   with a `ValueError` that names **all** missing variables. (FR-005, SC-005)
2. **Default**: Unset `DB_AUTH_MODE` resolves to `password`. (FR-006)
3. **Uniformity**: The selected mode applies to the async engine, the APScheduler job store, and
   Alembic migrations alike. (FR-007)
4. **Secret safety**: No token, password, or connection string is ever logged. (FR-008, SC-006)
5. **Auto-refresh**: For `gcp_iam`/`azure_entra`, short-lived credentials are refreshed transparently
   for new connections without restart. (FR-004, SC-004)

## Example configurations

```bash
# password (default)
DB_AUTH_MODE=password
DB_HOST=db.internal
DB_NAME=scheduler
DB_USER=scheduler
DB_PASSWORD=<secret>

# gcp_iam
DB_AUTH_MODE=gcp_iam
DB_GCP_INSTANCE_CONNECTION_NAME=my-proj:europe-west1:scheduler-pg
DB_GCP_IAM_USER=scheduler-sa@my-proj.iam
DB_GCP_PRIVATE_IP=true
DB_NAME=scheduler

# azure_entra
DB_AUTH_MODE=azure_entra
DB_HOST=scheduler-pg.postgres.database.azure.com
DB_NAME=scheduler
DB_AZURE_ENTRA_USER=scheduler-identity
DB_AZURE_MANAGED_IDENTITY_CLIENT_ID=00000000-0000-0000-0000-000000000000  # optional
```

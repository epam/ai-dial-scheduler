# Phase 1 Data Model: Workload-Identity Database Authentication

This feature introduces **no persisted entities** and **no database schema changes** — it only
affects how connections are authenticated. The "data model" here is the configuration/runtime model.

## Enum: `DbAuthMode` (extended)

`src/lib/db/enums.py`

| Value | String | Status |
|-------|--------|--------|
| `PASSWORD` | `password` | existing (default) |
| `CONNECTION_STRING` | `connection-string` | existing |
| `GCP_IAM` | `gcp_iam` | **new** |
| `AZURE_ENTRA` | `azure_entra` | **new** |

## Settings: `DatabaseSettings` (extended)

`src/lib/settings.py` — fields are read from environment (no `.env` reads of secret files by code).

| Field | Type | Default | Used by mode |
|-------|------|---------|--------------|
| `db_driver` | `DbDriver` | `ASYNC` | all |
| `db_host` | `str` | `localhost` | password, gcp_iam*, azure_entra |
| `db_port` | `int` | `5432` | password, azure_entra |
| `db_name` | `str` | `app` | password, gcp_iam, azure_entra |
| `db_user` | `str` | `app` | password |
| `db_password` | `str \| None` | `None` | password (required) |
| `db_url` | `str \| None` | `None` | connection-string (required) |
| `db_auth_mode` | `DbAuthMode` | `PASSWORD` | all |
| `db_gcp_instance_connection_name` | `str \| None` | `None` | **gcp_iam (required)** |
| `db_gcp_iam_user` | `str \| None` | `None` | **gcp_iam (required)** |
| `db_gcp_private_ip` | `bool` | `False` | **gcp_iam** |
| `db_azure_entra_user` | `str \| None` | `None` | **azure_entra (required)** |
| `db_azure_managed_identity_client_id` | `str \| None` | `None` | **azure_entra (optional, user-assigned MI)** |
| `db_pool_*`, `db_echo`, `db_pool_pre_ping` | various | existing | all |

\* For `gcp_iam`, host/port are not used for connection (the connector resolves the instance via
`db_gcp_instance_connection_name`); `db_name` is still required.

### Validation rules (`validate_auth_requirements`, mode → required fields)

| Mode | Required fields (must be non-None) |
|------|-----------------------------------|
| `password` | `db_password` |
| `connection-string` | `db_url` |
| `gcp_iam` | `db_gcp_instance_connection_name`, `db_gcp_iam_user` |
| `azure_entra` | `db_azure_entra_user` |

On a missing field the validator raises `ValueError` naming every missing field (fail-fast at
startup), consistent with the existing implementation.

## Runtime model

### `Database` (async singleton — `src/lib/db/engine.py`)

Extended to branch on `db_auth_mode`:

- `password` / `connection-string` → `_init_engine_url_based` (existing, asyncpg).
- `gcp_iam` → `_init_engine_gcp_iam` (async, Cloud SQL Connector `async_creator`).
- `azure_entra` → `_init_engine_azure_entra` (async, `async_creator` with fresh token password).

Holds and disposes mode-specific resources (GCP `Connector`, Azure async credential) in `dispose()`.

### Sync engine factory (`src/lib/db/sync_engine.py`)

`build_sync_engine(settings) -> Engine` branching on mode:

- `password` / `connection-string` → psycopg2 from `build_database_url(..., driver=SYNC)`.
- `gcp_iam` → pg8000 via Connector `creator` (`postgresql+pg8000://`).
- `azure_entra` → psycopg2 + `do_connect` event injecting fresh token as password.

### Job store factory (`src/lib/db/jobstore.py`)

`build_jobstore(settings) -> SQLAlchemyJobStore`:

- `password` / `connection-string` → `SQLAlchemyJobStore(url=<sync url>)` (unchanged behavior).
- `gcp_iam` / `azure_entra` → `SQLAlchemyJobStore(engine=build_sync_engine(settings))`.

### Credential providers (`src/lib/db/credentials.py`)

- GCP: lazy-created `Connector` holder (sync + async usage), disposed on shutdown.
- Azure: thin wrappers around `azure-identity` sync and `aio` `DefaultAzureCredential`, exposing
  `get_token_password()` for the connect hooks. Tokens are cached/refreshed by the SDK.

## State transitions

None. Connection authentication is stateless per connection; credential freshness is delegated to
the GCP connector and the Azure SDK token cache.

# Phase 0 Research: Workload-Identity Database Authentication

## Context recap

The scheduler opens PostgreSQL connections from three places:

1. **Async request-serving engine** — `src/lib/db/engine.py`, `create_async_engine` with asyncpg.
2. **Synchronous APScheduler job store** — `SQLAlchemyJobStore` built with a sync psycopg2 URL in
   `src/lib/scheduler.py` and `src/worker/app.py`.
3. **Alembic migrations** — `migrations/env.py` builds a sync psycopg2 URL.

The reference implementation (`ai-agent-manager-backend/src/db/engine.py`) only covers the **async**
GCP IAM case. This feature must also cover the **sync** path and add **Azure Entra**. Each decision
below resolves an unknown surfaced by that gap.

---

## Decision 1 — GCP IAM async engine

**Decision**: Reuse the reference pattern verbatim. Hold a singleton `google.cloud.sql.connector.
Connector` created on the running event loop with `refresh_strategy="LAZY"`; build the engine with
`create_async_engine("postgresql+asyncpg://", async_creator=getconn, ...)` where `getconn` calls
`connector.connect_async(instance_connection_name, "asyncpg", user=<iam_user>, db=<db_name>,
ip_type=PRIVATE|PUBLIC, enable_iam_auth=True)`.

**Rationale**: Proven in the sibling service; the connector handles IAM token minting and rotation
internally, so no custom refresh logic is needed. `async_creator` keeps SQLAlchemy pooling intact.

**Alternatives considered**: Manual IAM access-token generation + token-as-password — rejected; it
duplicates what the connector already does (certificate handling, instance discovery, rotation).

---

## Decision 2 — GCP IAM sync engine (job store + Alembic)

**Decision**: Use the Cloud SQL Connector's **pg8000** sync driver for this mode:
`Connector.connect(instance_connection_name, "pg8000", user=..., db=..., enable_iam_auth=True)` as
the `creator` for `create_engine("postgresql+pg8000://", creator=...)`. Pass this pre-built engine
to `SQLAlchemyJobStore(engine=...)` (the job store accepts an `engine=` argument in lieu of `url=`).

**Rationale**: The Cloud SQL Python Connector officially supports only **pg8000** (sync) and
**asyncpg** (async) for PostgreSQL — psycopg2 is not supported. APScheduler's `SQLAlchemyJobStore`
already supports being given an `Engine` directly, so no APScheduler patching is required. pg8000 is
a pure-Python driver compatible with SQLAlchemy 2.0.

**Alternatives considered**:
- Keep psycopg2 + manual IAM token-as-password — rejected; bypasses the connector's secure channel
  and instance resolution, and Cloud SQL IAM expects connector-mediated TLS.
- Make the job store async — rejected; APScheduler 3.x `SQLAlchemyJobStore` is synchronous by design.

**New dependency**: `cloud-sql-python-connector[pg8000]` (adds pg8000; asyncpg already present).

---

## Decision 3 — Azure Entra token acquisition

**Decision**: Use `azure-identity`. On the async path use `azure.identity.aio.DefaultAzureCredential`;
on the sync path use `azure.identity.DefaultAzureCredential`. Acquire a token for scope
`https://ossrdbms-aad.database.windows.net/.default` and use the token string as the connection
**password**; the **username** is the configured Entra principal (DB role). Support an optional
user-assigned managed-identity client id.

**Rationale**: `DefaultAzureCredential` resolves managed identity in-cluster (and developer
credentials locally) without code changes, and **caches tokens internally**, only refreshing when
near expiry — so calling `get_token(scope)` per new connection is cheap and always yields a valid
token. Microsoft's documented pattern is token-as-password with username = Entra principal.

**Alternatives considered**:
- `ManagedIdentityCredential` directly — viable but less flexible for local dev; `DefaultAzureCredential`
  is the recommended default and still uses managed identity in-cluster.
- Caching tokens ourselves with manual expiry tracking — rejected; the SDK already does this
  correctly, and re-implementing risks using stale/expired tokens.

**New dependency**: `azure-identity` (provides both sync and `aio` async credentials).

---

## Decision 4 — Azure Entra engine wiring (per-connection fresh token)

**Decision**:
- **Async (asyncpg)**: build with `create_async_engine("postgresql+asyncpg://", async_creator=...)`
  where the creator awaits the async credential's token, then `asyncpg.connect(host, port, db,
  user=<entra_user>, password=<token>, ssl="require")`.
- **Sync (psycopg2)**: build `create_engine(url_without_password)` and register a SQLAlchemy
  `do_connect` event listener that sets `cparams["password"] = <fresh token>` immediately before
  each new DBAPI connection.

**Rationale**: Tokens are valid 5–60 minutes, so the password cannot be baked into a static URL —
it must be resolved at connect time for every new pooled connection. `async_creator` (async) and the
`do_connect` event (sync) are the SQLAlchemy-sanctioned hooks for injecting per-connection
credentials, and they cooperate with connection pooling and `pool_pre_ping`/`pool_recycle`.

**Alternatives considered**:
- Static token in the URL refreshed by a background task that recreates the engine — rejected;
  racy, drops live connections, and far more complex than per-connect injection.
- psycopg2 `passfile`/`PGPASSWORD` env mutation — rejected; global, not connection-scoped, unsafe.

**Note**: psycopg2 is retained for the Azure sync path (standard Postgres + token password). Only
the **GCP** sync path switches to pg8000 (Decision 2).

---

## Decision 5 — Centralized, mode-aware construction

**Decision**: Introduce `src/lib/db/jobstore.py::build_jobstore(settings)` returning a configured
`SQLAlchemyJobStore` (using `url=` for password/connection-string and `engine=` for IAM/Entra), and
`src/lib/db/sync_engine.py` for the mode-aware sync `Engine`. `src/lib/scheduler.py`,
`src/worker/app.py`, and `migrations/env.py` all delegate to these. Credential/connector lifecycle
lives in `src/lib/db/credentials.py`.

**Rationale**: `scheduler.py` and `worker/app.py` currently **duplicate** the job-store URL wiring;
adding three more modes to both would multiply that duplication (DRY, Simplicity principles). A
single factory is the smallest change that keeps all three connection sites consistent.

**Alternatives considered**: Inline branching at each call site — rejected; violates DRY and risks
the three sites drifting apart as modes evolve.

---

## Decision 6 — Settings & enum surface

**Decision**: Extend `DbAuthMode` with `GCP_IAM = "gcp_iam"` and `AZURE_ENTRA = "azure_entra"`
(underscore spelling, matching the feature request and `DB_AUTH_MODE` env convention). Add to
`DatabaseSettings`:

- GCP: `db_gcp_instance_connection_name: str | None`, `db_gcp_iam_user: str | None`,
  `db_gcp_private_ip: bool = False`.
- Azure: `db_azure_entra_user: str | None`, `db_azure_managed_identity_client_id: str | None = None`.

Extend `validate_auth_requirements` so `gcp_iam` requires
`(db_gcp_instance_connection_name, db_gcp_iam_user)` and `azure_entra` requires `(db_azure_entra_user,)`
(host/port/name already exist and are reused). `db_password` stays `None` in these modes.

**Rationale**: Mirrors the reference service's settings shape for GCP and keeps fail-fast validation
consistent with the existing pattern. Reusing `db_host`/`db_port`/`db_name` for Azure avoids
redundant settings.

**Alternatives considered**: Nested per-mode sub-models — rejected as premature (YAGNI); flat
prefixed fields match the existing `DatabaseSettings` style.

---

## Resolved unknowns summary

| Unknown | Resolution |
|---------|-----------|
| GCP IAM async engine | Cloud SQL Connector `connect_async` + `async_creator` (reuse reference) |
| GCP IAM **sync** (job store/Alembic) | Connector **pg8000** driver + `SQLAlchemyJobStore(engine=...)` |
| Azure token source & scope | `azure-identity` `DefaultAzureCredential`, scope `…/ossrdbms-aad…/.default` |
| Azure async wiring | `async_creator` fetching fresh token per connect |
| Azure sync wiring | psycopg2 + `do_connect` event injecting fresh token |
| Avoid duplication across 3 connection sites | shared `build_jobstore` + `sync_engine` factory |
| New settings/enum | `gcp_iam`/`azure_entra` modes + GCP/Azure fields with fail-fast validation |
| New dependencies | `cloud-sql-python-connector[pg8000]`, `azure-identity` |

## Sources

- [Cloud SQL Python Connector — README (supported drivers, enable_iam_auth)](https://github.com/GoogleCloudPlatform/cloud-sql-python-connector/blob/main/README.md)
- [Connect using Cloud SQL Language Connectors (PostgreSQL)](https://docs.cloud.google.com/sql/docs/postgres/connect-connectors)
- [Connect with Managed Identity — Azure Database for PostgreSQL](https://learn.microsoft.com/en-us/azure/postgresql/security/security-connect-with-managed-identity)
- [Microsoft Entra Authentication concepts — Azure Database for PostgreSQL](https://learn.microsoft.com/en-us/azure/postgresql/security/security-entra-concepts)
- [Access token refresh samples with Entra ID for Azure PostgreSQL](https://github.com/Azure-Samples/Access-token-refresh-samples)

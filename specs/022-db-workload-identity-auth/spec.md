# Feature Specification: Workload-Identity Database Authentication

**Feature Branch**: `022-db-workload-identity-auth`

**Created**: 2026-06-18

**Status**: Draft

**Input**: User description: "In addition to DB_AUTH_MODE=password add support of azure_entra and gcp_iam that will use workload identity for db auth. Reuse existing approach from: ai-agent-manager-backend/src/db/engine.py"

## Overview

The scheduler currently connects to PostgreSQL using either a static password (`password` mode)
or a verbatim connection string (`connection-string` mode). Both require a long-lived secret to
be provisioned and rotated by operators. This feature adds two **secretless** authentication
modes that rely on the cloud platform's workload identity:

- **`gcp_iam`** — authenticates to Cloud SQL for PostgreSQL using the workload's Google service
  account (IAM database authentication), reusing the proven Cloud SQL Connector approach already
  shipped in the sibling `ai-agent-manager-backend` service.
- **`azure_entra`** — authenticates to Azure Database for PostgreSQL using a Microsoft Entra ID
  access token obtained from the workload's managed identity.

Both the API service and the background worker (including the scheduler's job store) must be able
to use these modes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deploy on GCP without database password (Priority: P1)

A platform engineer deploys the scheduler to a Google Cloud environment (e.g. GKE or Cloud Run)
where the workload runs as a Google service account that has been granted IAM access to a Cloud SQL
PostgreSQL instance. They configure the scheduler to use `gcp_iam` auth mode and provide the
instance connection name and IAM database user. No database password is stored anywhere.

**Why this priority**: GCP IAM is the explicitly named primary target and has a known-good
reference implementation to reuse, making it the lowest-risk, highest-value first slice.

**Independent Test**: Configure `gcp_iam` mode with a valid instance connection name and IAM user
against a Cloud SQL instance, start the service, and confirm it establishes database sessions and
serves traffic without any password configured.

**Acceptance Scenarios**:

1. **Given** the workload identity has IAM access to the Cloud SQL instance and `gcp_iam` mode is
   configured with the required settings, **When** the service starts, **Then** it connects to the
   database successfully and serves requests without a configured password.
2. **Given** `gcp_iam` mode is selected, **When** required settings (instance connection name, IAM
   user) are missing at startup, **Then** the service fails fast with a clear error naming the
   missing settings.
3. **Given** the service has been running long enough for the initial database credential to
   expire, **When** a new database connection is needed, **Then** authentication transparently
   uses a freshly obtained credential without operator intervention.

---

### User Story 2 - Deploy on Azure without database password (Priority: P2)

A platform engineer deploys the scheduler to an Azure environment where the workload has a managed
identity granted Entra ID access to an Azure Database for PostgreSQL instance. They configure
`azure_entra` auth mode. The service obtains an Entra access token via the managed identity and
uses it to authenticate; no password is stored.

**Why this priority**: Azure support broadens platform coverage but has no existing reference
implementation, so it carries more delivery risk and follows the GCP slice.

**Independent Test**: Configure `azure_entra` mode against an Azure PostgreSQL instance with a
managed identity that has database access, start the service, and confirm it connects and serves
traffic without a configured password.

**Acceptance Scenarios**:

1. **Given** the workload's managed identity has Entra access to the database and `azure_entra`
   mode is configured, **When** the service starts, **Then** it connects successfully without a
   configured password.
2. **Given** `azure_entra` mode is selected, **When** required settings are missing at startup,
   **Then** the service fails fast with a clear error naming the missing settings.
3. **Given** an obtained access token approaches expiry, **When** a new database connection is
   needed, **Then** a valid token is used so connections continue to succeed without operator
   intervention.

---

### User Story 3 - Existing password/connection-string deployments are unaffected (Priority: P1)

An operator running the scheduler with the existing `password` or `connection-string` mode
upgrades to the version that includes the new auth modes. Their configuration continues to work
exactly as before with no changes required.

**Why this priority**: Backward compatibility is mandatory; the new modes must be purely additive
and must not regress current deployments.

**Independent Test**: Start the service with an existing `password` (and separately
`connection-string`) configuration and confirm behavior is identical to the prior release.

**Acceptance Scenarios**:

1. **Given** a configuration using `password` mode, **When** the service starts, **Then** it
   behaves identically to the previous release.
2. **Given** no auth mode is explicitly configured, **When** the service starts, **Then** it
   defaults to `password` mode as it does today.

---

### Edge Cases

- **Workload identity not available / not authorized**: When the runtime has no usable workload
  identity, or the identity lacks database permission, the service must surface a clear,
  actionable error rather than hanging or emitting a cryptic stack trace.
- **Credential acquisition latency or failure at runtime**: If obtaining a token/credential fails
  for a new connection after startup, the failure must be logged clearly and the affected operation
  must fail gracefully (consistent with normal connection-failure handling), not silently stall.
- **Job store connections**: The background scheduler's persistent job store opens its own database
  connections (separate from the request-serving connections). These connections must authenticate
  using the same selected auth mode.
- **Misconfiguration across modes**: Selecting a workload-identity mode while also providing a
  password, or selecting `password`/`connection-string` while omitting their required fields, must
  be validated at startup with a clear message.
- **Credentials/tokens must never be logged**: Tokens, passwords, and connection secrets must not
  appear in logs at any level.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support two additional database authentication modes, `gcp_iam` and
  `azure_entra`, in addition to the existing `password` and `connection-string` modes.
- **FR-002**: In `gcp_iam` mode the system MUST authenticate to Cloud SQL for PostgreSQL using the
  workload's Google service-account IAM database authentication, without a configured password.
- **FR-003**: In `azure_entra` mode the system MUST authenticate to Azure Database for PostgreSQL
  using a Microsoft Entra ID access token obtained from the workload's managed identity, without a
  configured password.
- **FR-004**: The system MUST automatically obtain and refresh short-lived credentials/tokens so
  that long-running services continue to open new database connections without operator
  intervention or restarts.
- **FR-005**: The system MUST validate at startup that all settings required by the selected auth
  mode are present, and MUST fail fast with an error message that names the missing settings.
- **FR-006**: The existing `password` and `connection-string` modes MUST continue to work
  unchanged, and `password` MUST remain the default when no mode is configured.
- **FR-007**: Both the API service and the background worker MUST support all auth modes, including
  the scheduler's persistent job store connections.
- **FR-008**: The system MUST never log or otherwise expose tokens, passwords, or other connection
  secrets.
- **FR-009**: The selected auth mode and required parameters MUST be configurable through the
  service's existing environment-based configuration mechanism.
- **FR-010**: When credential acquisition or authentication fails, the system MUST emit a clear,
  actionable error identifying the auth mode and the likely cause (e.g. missing identity, missing
  database grant).

### Key Entities *(include if feature involves data)*

- **DB Auth Mode**: The selected strategy for authenticating to the database — one of `password`,
  `connection-string`, `gcp_iam`, or `azure_entra`.
- **Workload Identity Credential**: A short-lived, platform-issued credential (GCP IAM credential
  or Entra access token) obtained at runtime from the deployment environment and used in place of a
  static password.
- **Mode-specific configuration**: The set of settings each mode requires (e.g. for `gcp_iam`: the
  Cloud SQL instance connection name, the IAM database user, and IP-type selection; for
  `azure_entra`: the managed-identity and target-database parameters needed to request a token).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A scheduler instance deployed on GCP with `gcp_iam` mode and a correctly granted
  workload identity connects to the database and serves traffic with zero database passwords stored
  in configuration or secrets.
- **SC-002**: A scheduler instance deployed on Azure with `azure_entra` mode and a correctly
  granted managed identity connects to the database and serves traffic with zero database passwords
  stored in configuration or secrets.
- **SC-003**: Existing deployments using `password` or `connection-string` mode require no
  configuration changes and exhibit identical behavior after upgrading.
- **SC-004**: A service running continuously past the lifetime of a single issued credential
  continues to open new database connections successfully with no operator action and no errors
  attributable to expired credentials.
- **SC-005**: Starting the service with an incomplete configuration for the selected mode produces
  a startup error that names every missing required setting, in 100% of misconfiguration cases.
- **SC-006**: No token, password, or connection secret appears in service logs at any log level
  during connection, refresh, or failure handling.

## Assumptions

- The deployment platform provides the workload identity out-of-band (GCP service account bound to
  the workload; Azure managed identity assigned to the workload). Provisioning and granting these
  identities is an operator responsibility and is out of scope for this feature.
- The GCP implementation reuses the Cloud SQL Connector based approach from the sibling
  `ai-agent-manager-backend` service (`src/db/engine.py`), including private/public IP selection.
- The Azure implementation follows the standard Entra-token-as-password pattern using the
  platform's managed identity; there is no existing in-repo reference, so it is built fresh.
- The same selected auth mode applies to all database connections within a service instance
  (request-serving async connections and the synchronous scheduler job-store connections); per-
  connection mixing of modes is out of scope.
- Mode-specific client libraries (e.g. the Cloud SQL Connector for GCP, the Azure identity library
  for Azure) are acceptable additional dependencies.
- Database-side configuration (enabling IAM/Entra authentication, creating the database role for
  the identity, granting privileges) is performed by operators and is out of scope.
- Local development and tests continue to use `password`/`connection-string` modes; the workload-
  identity modes are validated in their respective cloud environments.

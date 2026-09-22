# Feature Specification: AWS RDS IAM Database Authentication

**Feature Branch**: `038-db-aws-iam-auth`

**Created**: 2026-09-16

**Status**: Draft

**Input**: User description: "Добавить поддержку aws как у gcp и azure для асинк и синк"

## Overview

The scheduler supports four database authentication modes today: `password`, `connection-string`,
`gcp_iam` and `azure_entra` (see `specs/022-db-workload-identity-auth`). Deployments on AWS have no
secretless option — they must provision and rotate a static `DB_PASSWORD`.

This feature adds a fifth mode, **`aws_iam`**, which authenticates to Amazon RDS / Aurora for
PostgreSQL using an IAM authentication token generated from the workload's own AWS credentials
(IRSA on EKS, task role on ECS, instance profile on EC2). The token replaces the password on every
new connection; no secret is stored.

The mode must work on both database access paths the service maintains, exactly like `gcp_iam` and
`azure_entra` do:

- the **async** engine (asyncpg) used by repositories, services and health checks in both the API
  and the worker;
- the **sync** engine (psycopg2) used by the APScheduler `SQLAlchemyJobStore` in both processes, and
  by Alembic's offline path exclusion rules.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deploy on AWS without a database password (Priority: P1)

A platform engineer deploys the scheduler to EKS where the pod's service account is annotated for
IRSA and the corresponding IAM role holds `rds-db:connect` on the target database user. They set
`DB_AUTH_MODE=aws_iam`, the host, the database name and the IAM database user. No password is
configured anywhere.

**Why this priority**: This is the entire point of the feature — a secretless AWS deployment.

**Independent Test**: Configure `aws_iam` against an RDS instance with IAM auth enabled, start the
API and the worker, and confirm both serve traffic and execute scheduled jobs with no password set.

**Acceptance Scenarios**:

1. **Given** the workload's IAM role holds `rds-db:connect` for the configured database user and
   `aws_iam` mode is configured, **When** the API starts, **Then** it opens database sessions and
   serves requests without a configured password.
2. **Given** `aws_iam` mode is selected, **When** `DB_AWS_IAM_USER` is missing at startup,
   **Then** startup fails with an error naming the missing setting.
3. **Given** `aws_iam` mode is selected and `DB_PASSWORD` or `DB_URL` is also set, **Then** startup
   fails — the mode is secretless and a configured secret is contradictory.
4. **Given** the service has run longer than the 15-minute IAM token lifetime, **When** a new
   database connection is opened, **Then** a freshly generated token is used without operator
   intervention.

---

### User Story 2 - Worker job store on AWS IAM (Priority: P1)

The same deployment's worker persists and reads APScheduler jobs through the synchronous job store.

**Why this priority**: Without the sync path the worker cannot read the job store, so the feature
would be useless in the same deployment that User Story 1 enables.

**Independent Test**: With `aws_iam` configured, start the worker and confirm it reads jobs written
by the API and executes them.

**Acceptance Scenarios**:

1. **Given** `aws_iam` mode, **When** the APScheduler job store opens a new connection, **Then** a
   freshly generated IAM token is injected as the connection password over a TLS connection.
2. **Given** `aws_iam` mode and a configured `DB_SCHEMA`, **When** any new connection is opened on
   either engine, **Then** `search_path` is set to that schema.

---

### User Story 3 - Region resolution (Priority: P2)

An operator deploying on ECS relies on the platform-provided `AWS_REGION`; another operator runs the
service where no region is exported and must set it explicitly.

**Why this priority**: A wrong or unresolved region produces a token signed for the wrong region and
an authentication failure that is hard to diagnose. It must fail loudly at startup instead.

**Acceptance Scenarios**:

1. **Given** `DB_AWS_REGION` is unset but the environment provides a region (e.g. `AWS_REGION`),
   **When** the engine is built, **Then** the resolved region is used.
2. **Given** neither `DB_AWS_REGION` nor any environment region is resolvable, **When** the engine
   is built, **Then** it fails fast with an error telling the operator to set `DB_AWS_REGION`.
3. **Given** `DB_AWS_REGION` is set, **When** the engine is built, **Then** it takes precedence over
   the environment.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support an additional database authentication mode `aws_iam` alongside
  `password`, `connection-string`, `gcp_iam` and `azure_entra`.
- **FR-002**: In `aws_iam` mode the system MUST authenticate to Amazon RDS / Aurora for PostgreSQL
  using an IAM authentication token generated from the workload's AWS credential chain, without a
  configured password.
- **FR-003**: A fresh IAM token MUST be generated for every new database connection on both the
  async and the sync engine, so that long-running services keep connecting past the token's
  15-minute lifetime without a restart.
- **FR-004**: All connections in `aws_iam` mode MUST be encrypted (TLS), because RDS rejects IAM
  authentication over an unencrypted connection.
- **FR-005**: The system MUST validate at startup that `DB_AWS_IAM_USER` is present and that neither
  `DB_PASSWORD` nor `DB_URL` is set, failing fast with an error naming the offending settings.
- **FR-006**: The AWS region MUST be taken from `DB_AWS_REGION` when set and otherwise resolved from
  the ambient AWS configuration; if it cannot be resolved the system MUST fail fast with an
  actionable error.
- **FR-007**: The existing four modes MUST continue to work unchanged, and `password` MUST remain
  the default.
- **FR-008**: Both the API service and the worker MUST support `aws_iam`, including the APScheduler
  job store's synchronous connections and Alembic's online migration path.
- **FR-009**: The system MUST never log the generated token, the password, or the connection string.
  Credential-acquisition failures MUST log the auth mode and the exception type only.
- **FR-010**: Token generation MUST NOT block the event loop on the async path.

### Key Entities

- **DB Auth Mode**: extended with `aws_iam`.
- **AWS RDS IAM token**: a short-lived (15 min) presigned credential generated locally from the
  workload's AWS credentials and used as the PostgreSQL password.
- **Mode-specific configuration**: `DB_AWS_IAM_USER` (required), `DB_AWS_REGION` (optional), plus
  the shared `DB_HOST`, `DB_PORT`, `DB_NAME`.

## Success Criteria *(mandatory)*

- **SC-001**: With `aws_iam` configured against an IAM-enabled RDS instance, the API serves requests
  and the worker executes jobs with no `DB_PASSWORD` set.
- **SC-002**: A service running longer than 15 minutes opens new connections successfully without a
  restart.
- **SC-003**: Missing `DB_AWS_IAM_USER` or a co-configured `DB_PASSWORD`/`DB_URL` aborts startup
  with a message naming the setting.
- **SC-004**: An unresolvable region aborts engine construction with a message naming
  `DB_AWS_REGION`.
- **SC-005**: No test or manual inspection finds a token, password or connection string in the logs.
- **SC-006**: `password`, `connection-string`, `gcp_iam` and `azure_entra` behaviour is unchanged
  (existing unit and integration tests keep passing).

## Out of Scope

- RDS Proxy-specific configuration.
- Certificate pinning against the RDS CA bundle (`sslmode=verify-full`). Connections require TLS but
  do not verify the server certificate, mirroring the existing `azure_entra` path. Changing this is
  a separate, cross-cutting decision for all cloud modes.
- Alembic **offline** migrations for `aws_iam` — like the other workload-identity modes it has no
  static URL and stays unsupported offline.
- IAM authentication for anything other than PostgreSQL.

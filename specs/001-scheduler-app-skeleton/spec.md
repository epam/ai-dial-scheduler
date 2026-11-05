# Feature Specification: Scheduler Application Skeleton

**Feature Branch**: `001-scheduler-app-skeleton`

**Created**: 2026-06-09

**Status**: Draft

**Input**: User description: "Create a scheduler application skeleton. Scheduler is an application which allows to schedule IO bound jobs and run them in the background within DIAL infrastructure. Application should consist of two parts. API which schedules the job (currently api with no specific logic and should contain no routes regarding scheduling) and separate worker which actually runs them. Jobs should be stored in PostgreSQL. Application should run locally by one click using docker compose with all required component (api, worker, database). Quality gates should be established by linters, formatters type checks and unit tests. Readme should describe how to run application, its components and quality checks/tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Developer Establishes and Validates the Scheduler Foundation (Priority: P1)

A developer clones the repository and, using a single command, starts the entire application stack — the API service, the background worker, and the database — all running together locally. With the application running, the developer can confirm the API is healthy and the worker is active. The developer can also run all quality gates (code style checks, type analysis, unit tests) from the README instructions and see them all pass. The README alone is sufficient for a new team member to understand what each component does and how to operate the system.

**Why this priority**: This is the entire scope of the skeleton — without a runnable, documented, quality-gated foundation, no future scheduling features can be built reliably.

**Independent Test**: Can be fully tested by running `docker compose up` against the repository and verifying: (1) all services start without errors, (2) the API health endpoint responds, (3) the worker process is alive, (4) all quality gate commands complete successfully.

**Acceptance Scenarios**:

1. **Given** a fresh clone of the repository with no prior local setup, **When** the developer runs the single start command, **Then** all three components (API, worker, database) start successfully within 2 minutes with no manual intervention.
2. **Given** the application is running, **When** the developer queries the API health endpoint, **Then** the response confirms the service is operational.
3. **Given** the application is running, **When** the developer inspects the worker component, **Then** it is alive and ready to process background jobs.
4. **Given** the source code, **When** the developer runs the code-style check command from the README, **Then** no style violations are reported.
5. **Given** the source code, **When** the developer runs the type-analysis command from the README, **Then** no type errors are reported.
6. **Given** the source code, **When** the developer runs the unit test command from the README, **Then** all tests pass and a coverage report is produced.
7. **Given** a developer unfamiliar with the project, **When** they read only the README, **Then** they understand the purpose of each component and can run the application and all quality checks without outside assistance.
8. **Given** the application starts for the first time, **When** the database initialises, **Then** the required schema is applied automatically with no manual migration steps.

---

### Edge Cases

- What happens when the database is unavailable at the time the API or worker starts — do they retry, crash, or block startup?
- What happens when a required environment variable is missing at container startup?
- What happens when the API service starts before the database is ready (race condition on fresh `docker compose up`)?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST consist of two independently deployable services: an API service and a background worker service.
- **FR-002**: Both services and the database MUST be startable together with a single command using the provided local orchestration configuration; no additional manual steps are permitted.
- **FR-003**: The API service MUST expose a health check endpoint that returns a successful response when the service is operational.
- **FR-004**: The worker service MUST start successfully and enter a ready state in which it is capable of processing background jobs.
- **FR-005**: The system MUST persist job records in a relational database; the schema MUST be applied automatically on first startup.
- **FR-006**: The API service MUST NOT expose any job-scheduling routes in this skeleton; its structure is established solely for future extension.
- **FR-007**: The codebase MUST pass all code-style and formatting checks without errors when the corresponding quality-gate command is run.
- **FR-008**: The codebase MUST pass static type analysis without errors when the corresponding quality-gate command is run.
- **FR-009**: All unit tests MUST pass and a coverage report MUST be produced when the test command is run.
- **FR-010**: A README MUST be present that documents: how to start the full application locally, the purpose of each component (API, worker, database), and the exact commands to run each quality gate.
- **FR-011**: All service configuration (database credentials, ports, etc.) MUST be injectable via environment variables; no secrets or environment-specific values may be hard-coded.

### Key Entities

- **Job**: A unit of background work to be scheduled and executed. Attributes: unique identifier, type/kind, current status, creation timestamp, scheduled execution time, last-updated timestamp. A Job transitions through a defined set of statuses (e.g., pending, running, completed, failed).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer with no prior setup can start all application components using a single command in under 2 minutes on a standard development machine.
- **SC-002**: The API service health endpoint responds with a success indicator within 5 seconds of the service reaching a ready state.
- **SC-003**: All quality gates (style checks, type analysis, unit tests) complete without any failures or errors.
- **SC-004**: A developer unfamiliar with the codebase can run the full application and all quality checks using only the README, without consulting any team member.
- **SC-005**: The database schema is applied automatically on first startup; no manual migration commands are needed by the developer.

## Assumptions

- The application is intended to operate within the DIAL infrastructure platform; authentication and authorisation with DIAL are out of scope for this skeleton and will be added in a subsequent feature.
- No job-scheduling API routes are in scope; the API skeleton exists only to establish the service structure for future extension.
- The local development environment is assumed to have a container runtime available (e.g., Docker Desktop or equivalent); no host-level database or runtime installation is required.
- Environment-specific configuration is injected exclusively via environment variables; a sample environment file (`.env.example`) with placeholder values is considered sufficient for local setup documentation.
- The Job entity schema is intentionally minimal for this skeleton and will be extended as scheduling features are introduced.
- Both services share the same codebase repository; a mono-repo layout with shared library code is the intended structure.

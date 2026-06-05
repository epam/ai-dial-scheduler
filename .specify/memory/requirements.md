# DIAL Scheduler Engine — 

**Status:** Draft  
**Date:** 2026-05-15  
**Context:** Standalone scheduling engine designed to integrate with [EPAM DIAL](https://dialx.ai/), providing dynamic trigger scheduling with pluggable action dispatch.

---

## 1. Background & Motivation

EPAM DIAL is an AI application platform that currently has no native scheduling capabilities. Several use cases require time-driven automation, and they differ primarily in how execution is handled:

- **Project-specific execution services** — an external service already handles the work; the scheduler's only job is to call a configured HTTP endpoint at the right time.
- **Native DIAL execution** — running a DIAL QuickApp or pipeline on a schedule, with no separate execution service involved.

The scheduler is designed to serve both cases from a single lightweight engine by making the action dispatch mechanism pluggable.

DIAL is also deployed in configurations that emulate multi-tenancy, where scheduled triggers must be scoped to a tenant, folder, or owner. This is a cross-cutting concern: it applies regardless of which execution model is in use. The scheduler addresses it through an opaque metadata system rather than any built-in notion of tenancy.

---

## 2. Goals & Non-Goals

### Goals

- Provide an API for full lifecycle management of scheduled triggers.
- Support flexible schedule definitions — both recurring patterns and one-time fires — with timezone awareness.
- Dispatch configured actions reliably when triggers fire (at-least-once semantics).
- Allow arbitrary metadata on triggers to support folder structures and multi-tenancy.
- Remain lightweight: reuse DIAL's existing technological dependencies where possible, introduce new ones only if absolutely necessary.
- Be deployable in any cloud or on-premises environment without vendor-specific dependencies.
- Provide enough observability to diagnose missed or failed fires in production.

### Non-Goals

- The scheduler does not interpret or validate the business meaning of metadata values.
- The scheduler does not orchestrate multi-step workflows or maintain state between firings.
- The scheduler does not manage or verify user identities. Identity context for a fired action (e.g., whose permissions it runs under) is the caller's responsibility — in DIAL deployments, task identity is typically bound to the MCP server rather than carried by the scheduler process.
- Exact-once delivery is not guaranteed; consumers of fired actions must be idempotent where it matters.

---

## 3. Functional Requirements

### 3.1 Trigger Management

- The scheduler MUST provide an API for creating, reading, updating, deleting, and listing triggers.
- Each trigger MUST carry: a unique identifier, a human-readable name, a schedule definition with timezone, an action descriptor, a metadata map, and an enabled/paused state.
- Individual fields of a trigger (e.g., enabled state, schedule) MUST be updatable independently without replacing the full trigger definition.
- The API MUST support listing and filtering triggers by metadata key-value pairs.

### 3.2 Scheduling

- The schedule definition format MUST support both recurring patterns (e.g., "every weekday at 8am") and one-time fires at a specific point in time. Timezone-aware evaluation is required.
- The engine MUST provide at-least-once firing semantics. Triggers missed during downtime MUST be recovered after restart, within a configurable catch-up window.
- Per-trigger retry behavior on action failure MUST be configurable.

### 3.3 Action Dispatch

- Each trigger MUST have a single typed action that defines what happens when the trigger fires.
- Action dispatch MUST be non-blocking with respect to the scheduling loop — a slow action target MUST NOT delay other triggers from firing.
- At minimum, the scheduler MUST support outbound HTTP calls as the dispatch mechanism, forwarding trigger context to a configured endpoint. This must cover both fire-and-forget patterns (where the target acknowledges immediately and processes asynchronously) and long-running calls (where the scheduler waits for the response, e.g., when invoking a DIAL application that streams its output).
- The action model MUST be extensible so that new dispatch mechanisms can be added without changes to the core trigger model.

### 3.4 Metadata

- Each trigger MUST support an opaque key-value metadata map.
- The scheduler MUST store and filter on metadata but MUST NOT interpret or validate its content.
- Typical uses include encoding folder hierarchy, tenant identity, owner, or environment — but these are caller conventions, not engine concepts.

### 3.5 Observability

- The engine MUST maintain a queryable history of fire attempts per trigger, including outcome, timing, and error detail.
- The engine MUST emit structured logs for every fire attempt.
- The engine SHOULD expose operational metrics (fire rate, failure rate, scheduling lag), ideally segmentable by trigger metadata to support per-tenant or per-folder observability.
- The engine MUST expose health and readiness endpoints suitable for use in container orchestration.

---

## 4. Non-Functional Requirements

- **Lightweight footprint** — the scheduler should prefer storage primitives DIAL already provides (Redis, blob/file storage). Introducing a standard technology like a relational database is acceptable if justified. Adding infrastructure with significant operational complexity — message brokers, stream processors, distributed coordination services — is not.
- **Cloud-agnostic** — deployable as a container on any platform without cloud-vendor services or SDKs.
- **Horizontally scalable** — multiple replicas MUST be safe to run concurrently without duplicate fires or data loss.
- **Low scheduling lag** — the delay between a scheduled cron tick and actual action dispatch should be small (seconds, not minutes) under normal load.
- **DIAL-decoupled** — the scheduler MUST have no dependency on DIAL internals. Integration with DIAL happens exclusively through DIAL's public API.
- TODO: License
- TODO: MCP



- TODO: Check out MCP Apps
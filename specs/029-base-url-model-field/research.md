# Research: Base URL and Payload Model for Chat Completion

**Feature**: `029-base-url-model-field` | **Date**: 2026-07-22

No open `NEEDS CLARIFICATION` markers remained in the spec. This research records the design
decisions grounded in the existing codebase (the `responses` target type already implements the
target pattern).

## Decision 1 — Use `base_url` for chat completion, drop URL-path deployment parsing

**Decision**: In `ChatCompletionExecutor.execute`, construct
`AsyncAzureOpenAI(base_url=properties.url, api_key=..., api_version=..., http_client=...)` and let the
SDK append the `/chat/completions` path. Remove the `parse_deployment(properties.url)` call and the
`azure_endpoint`/`azure_deployment` arguments.

**Rationale**: `ResponsesExecutor` (`src/worker/executors/responses.py:22-30`) already uses exactly
this pattern for the sibling API. Mirroring it gives a single, consistent construction path and a
single URL convention across both DIAL LLM target types. The deployment is instead selected by the
`model` field the SDK sends in the request body (from `payload`).

**Alternatives considered**:
- *Keep `azure_deployment` but source it from `payload.model`*: rejected — `azure_deployment` still
  bakes the deployment into the URL path (`/deployments/{model}/...`), which contradicts the goal of a
  base URL and diverges from the responses pattern.
- *Support both legacy deployment-in-URL and new base-URL forms*: rejected per spec Assumptions
  (deliberate breaking change; dual support adds branching and validation ambiguity for no current use
  case — YAGNI).

## Decision 2 — Require `payload.model` for `chat_completion`

**Decision**: Extend the model validator in `src/lib/schemas/schedule.py` so the existing
responses-only rule (`payload.model` required) applies to `chat_completion` as well.

**Rationale**: With the deployment no longer in the URL, `payload.model` is the only source of the
deployment for chat completion. Requiring it at validation time fails fast at schedule
creation/update, before any run is attempted (spec FR-003, SC-002). The rule already exists for
`responses` — this generalizes the condition to both types.

**Alternatives considered**:
- *Validate only at run time*: rejected — later failure, worse UX, violates fail-fast; contract tests
  expect creation-time rejection.

## Decision 3 — Resolve conversation deployment from `payload.model`

**Decision**: In `create_run_conversation` (`src/worker/conversation.py:127-131`), replace
`extract_deployment_id(properties.url)` with the `model` value from `properties.payload`. Keep the
existing application-prefix logic (`APPLICATION_DEPLOYMENT_PREFIX` → resolve `reference` via
`get_application`) and the non-fatal failure behavior.

**Rationale**: Conversation creation must attribute the saved conversation to the same deployment the
run called. Since the run now takes the deployment from `payload.model`, conversation creation must
use the same source to stay consistent (spec FR-006). The downstream application-vs-model branching is
unchanged — only the source of the identifier changes.

**Edge handling**: If `payload.model` is absent, `ConversationCreationError` is raised (mirroring the
current `ValueError` path), which the caller already treats as non-fatal. In practice validation
(Decision 2) guarantees `payload.model` is present for chat-completion schedules.

**Alternatives considered**:
- *Keep parsing the URL for conversation only*: rejected — the base URL no longer carries a deployment
  segment, so parsing would fail; also inconsistent with the executed request.

## Decision 4 — Remove `parse_deployment` and `extract_deployment_id`

**Decision**: Delete both helpers from `src/lib/utils/dial.py` (keep `strip_bearer_prefix`) and remove
their unit tests.

**Rationale**: After Decisions 1 and 3, the only two callers
(`chat_completion.py`, `conversation.py`) no longer use them. Leaving dead code violates Simplicity &
YAGNI. Verified callers via repo-wide search: only those two modules and their tests reference the
helpers.

**Alternatives considered**:
- *Keep the helpers "just in case"*: rejected — YAGNI; dead code is a maintenance and confusion cost.

## Decision 5 — No database migration

**Decision**: No Alembic migration.

**Rationale**: `target_type`, `url`, and `payload` are stored in the JSONB `kwargs` column of the
schedule. This change only alters how those existing fields are interpreted and validated, not the
schema. Consistent with the `028-responses-schedule-type` approach.

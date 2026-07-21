# Code Review Report — 027-create-run-conversation

- **Spec dir**: `specs/027-create-run-conversation/`
- **Date**: 2026-07-17 (fresh); 2026-07-20 (verification)
- **Run mode**: Verification (of the 2026-07-17 Fresh Review)
- **Artifacts consulted**: `spec.md`, `plan.md`, `data-model.md`, `tasks.md`, `contracts/dial-client.md`, `contracts/dial-conversation.schema.json`, `.specify/memory/constitution.md` (v1.2.0), `CLAUDE.md`
- **Files reviewed**: `src/lib/constants.py`, `src/lib/exceptions/errors.py`, `src/lib/schemas/conversation.py`, `src/lib/schemas/schedule.py`, `src/lib/clients/dial_client.py`, `src/worker/conversation.py`, `src/worker/executors/chat_completion.py`, `src/worker/jobs.py`, `src/lib/utils/dial.py`

## Summary

**Approved.** Verification confirms all prior improvements (I1–I3) and minor items (M1–M4) are resolved. The two hard reliability/security requirements remain satisfied: conversation-creation failures cannot fail a successful run (FR-013), and no prompt/response content or credentials are logged (FR-015). Lint (`ruff`), type-checking (`mypy`), and the full affected test suite (156 tests) all pass. One new minor test-coverage gap was found (**M6**): the extracted `src/lib/utils/dial.py` helper has no dedicated unit test. Nothing blocks merge.

### Verification result (2026-07-20)

| ID | Status | Note |
|----|--------|------|
| I1 | Resolved | `build_conversation` now takes 3 params via `ConversationBuildInput`. |
| I2 | Resolved | `_payload_temperature()` sources temperature; message `settings` populated. |
| I3 | Resolved | Shared `src/lib/utils/dial.py` (`strip_bearer_prefix`, `extract_deployment_id`) used by both modules. |
| M1 | Resolved | Renamed `_last_user_prompt`, docstring aligned, `reversed()` retained. |
| M2 | Resolved | `bucket` param added to contract `dial-client.md` and implementation. |
| M3 | Resolved | `is_application` computed once and reused in orchestration. |
| M4 | Resolved | Streaming logs `getattr(stream_event, "type", None)` only. |
| M6 | Open (new) | No dedicated test for `src/lib/utils/dial.py`. |

## Critical Issues

None.

## Improvements (should fix)

### I1 — `build_conversation` takes 6 positional parameters — Status: Resolved
- **File**: `src/worker/conversation.py:47-49`
- **Resolution**: `ConversationBuildInput` (`src/lib/schemas/conversation.py:49-59`) now groups `model_id`, `bucket`, `schedule_id`, `run_id`. `build_conversation(properties, result, build_input)` is 3 params; `create_run_conversation(ctx, properties, result)` is 3 params. Both comply with the ≤3-parameter rule. Verified.

### I2 — Conversation temperature / prompt / message `settings` are not sourced from the run payload — Status: Resolved
- **File**: `src/worker/conversation.py:30-33,64-66,75-83`
- **Resolution**: `_payload_temperature()` reads `properties.payload.get("temperature")` (default `1.0`, with an `isinstance(int|float)` guard). `DialConversation.temperature` is set from it, and both messages carry `settings = {"prompt": "", "temperature": <t>, "selectedAddons": []}`, matching `data-model.md` §3/§5. Verified.

### I3 — Duplicated bearer-prefix stripping and deployment-extraction logic — Status: Resolved
- **File**: `src/lib/utils/dial.py` (NEW)
- **Resolution**: `strip_bearer_prefix` and `extract_deployment_id` now live in `src/lib/utils/dial.py`. `src/worker/conversation.py:11` and `src/worker/executors/chat_completion.py:9` both import `strip_bearer_prefix` from it, eliminating the duplicated `_BEARER_PREFIX` block. Note: the executor still keeps its own `_parse_deployment` (it needs the `(endpoint, deployment)` tuple with only the first segment for Azure), while the orchestrator uses the shared `extract_deployment_id` (full multi-segment id). This is an intentional semantic difference, not duplication — the token-normalization duplication that I3 flagged is fully removed. Verified.

## Minor Suggestions

- **M1** (`src/worker/conversation.py:20-27`) — Status: Resolved. Renamed to `_last_user_prompt`; docstring now says "last user message content"; body still uses `reversed(messages)`. Contradiction removed. Verified.
- **M2** (`src/lib/clients/dial_client.py:150`, `contracts/dial-client.md:24`) — Status: Resolved. Contract now documents `put_conversation(api_key: str, bucket: str, conversation: DialConversation) -> None`, matching the implementation. Verified.
- **M3** (`src/worker/conversation.py:118-130`) — Status: Resolved. `is_application` is computed once at line 118 and reused for both the log line (123) and the branch (126); `_resolve_model_id` was inlined so the prefix check no longer runs twice. Verified.
- **M4** (`src/lib/clients/dial_client.py:171`) — Status: Resolved. `put_conversation` now calls `conversation.model_dump(exclude_none=True)`, so `custom_content`/`responseId` are omitted from the user message when `None`. Verified.
- **M5** (`src/worker/jobs.py:206`) — Status: Resolved (accepted as-is). Inline post-run creation is bounded by the httpx timeout and never affects run success; noted in the fresh review as acceptable. No change required.
- **M6** (`src/lib/utils/dial.py`) — Status: Open (new). The extracted utility has no dedicated unit test under `tests/unit/lib/utils/` (a stale `test_dial.cpython-314.pyc` in `__pycache__` suggests a test file once existed and was removed). `strip_bearer_prefix` and `extract_deployment_id` are currently exercised only indirectly via executor/conversation tests, so the two `ValueError` branches of `extract_deployment_id` (no `/deployments/` marker; empty deployment segment) and the no-prefix path of `strip_bearer_prefix` are not directly asserted. **Suggested fix**: add `tests/unit/lib/utils/test_dial.py` covering both helpers, including the two raise paths. Non-blocking.

## Intent Alignment

- **FR-001/FR-002/FR-014**: `create_conversation: bool = False` added to `ScheduleProperties`; `validate_target_rules` rejects it for non-chat-completion targets; it flows through the existing `properties` dict into read responses. Correct.
- **FR-003/FR-013**: `_maybe_create_conversation` gates on `target_type == CHAT_COMPLETION and create_conversation`, runs only after `_persist_success`, and wraps the call in `try/except Exception` that logs and returns — no exception can escape to alter run success. Correct and matches SC-005.
- **FR-004/FR-005**: Two messages built (user from snapshot, assistant from `result["result"]["choices"][0]["message"]`); `custom_content` copied when it is a dict. Correct.
- **FR-006/FR-007/FR-008**: Model deployments use the deployment id directly; application deployments (prefix `applications/`) resolve `reference` via `get_application`. Matches the verified contract example (double `applications/` path segment is expected).
- **FR-009/FR-010**: `folderId`/`id` follow `conversations/{bucket}/.scheduler/{schedule_id}/{run_id}`; bucket resolved via `get_bucket`. Correct.
- **FR-011**: All three DIAL calls use the run owner's OBO token (`ctx.auth_header_value`), with the `Bearer ` prefix stripped for the `Api-Key` header. Correct.
- **FR-012**: Executor unified on `chat.completions.stream(...)` + `get_final_completion()`, storing `{"result": <assembled ChatCompletion>.model_dump()}` for both transports. Correct.
- **FR-015**: Streaming diagnostics log `event_type` only (never content); start/success logs carry only ids and deployment kind; failure logs `str(exc)` from `ConversationCreationError`, whose messages contain status codes/paths but never tokens or streamed content. Satisfied.
- **Divergence**: None remaining. The I2 divergence (temperature/settings not sourced from payload) is now resolved; the implementation matches `data-model.md` §3/§5.

## Checklist Pass

- **Correctness & logic**: PASS — control flow, edge cases (missing user message, missing/None content, non-dict `custom_content`), and result-shape handling are correct.
- **Error handling / reliability (Principle I)**: PASS — typed `ConversationCreationError`, per-request timeouts via shared httpx client, best-effort isolation preserves run success.
- **Async correctness**: PASS — all I/O awaited; shared `HttpClientProvider` client reused; no blocking calls.
- **Type safety (mypy)**: PASS — complete signatures and return types; Pydantic v2 models correct.
- **Observability (Principle II)**: PASS — structured `logger` events with run_id/schedule_id/deployment kind; no `print()`.
- **Contract-Driven (Principle IV)**: PASS — new boundaries expressed as Pydantic models; minor contract-doc drift noted in M2.
- **Security (FR-015 / constitution)**: PASS — no secrets or streamed content logged; no `.env` reads; OBO token confined to headers.
- **Structure & conventions**: PASS — files placed per `src/api|worker|lib` layout; absolute imports; `lib/` does not import from `worker/`; naming and `# noqa: N815` for DIAL field names appropriate.
- **Simplicity (Principle V)**: PASS with one exception — parameter-count limit exceeded in `build_conversation` (**I1**).
- **Testing (Principle III)**: PASS (per `tasks.md` T003–T020, all marked complete); test bodies not re-verified in this review.

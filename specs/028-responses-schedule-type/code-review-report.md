# Code Review Report: Responses Schedule Type

- **Spec dir**: `specs/028-responses-schedule-type/`
- **Date**: 2026-07-21
- **Run mode**: Verification (re-review #2 after runtime-bug fix C1); prior verification resolved I1/I2; originally Fresh Review on 2026-07-21
- **Artifacts consulted**: `spec.md`, `plan.md`, `tasks.md`, `contracts/responses-executor.md`, `res.py` (reference example)
- **Change set**: working-tree diff on branch `028-responses-schedule-type` vs `development` (no commits yet; all changes uncommitted)

## Summary

**Approved.** The implementation is a clean, faithful, additive slice reusing the existing validation, retry/timeout orchestration, and OBO auth path. A runtime bug (C1 — Responses API uses a base-URL + `payload.model` pattern, not the Azure per-deployment URL) was found after the previous approval and has now been **fixed and verified**. The chat-completion deployment-URL parsing path is fully reverted to its original behavior and confirmed unregressed. Type checking (`mypy`, 77 files), linting (`ruff`), and all relevant unit tests pass (102 in the re-verified dial/executor/schema scope).

## Critical Issues

### C1 — Responses executor wrongly reused Azure per-deployment URL parsing — Status: Resolved
- **File**: `src/worker/executors/responses.py`, `src/lib/utils/dial.py`, `src/lib/schemas/schedule.py`
- **Original problem**: The executor built `AsyncAzureOpenAI` with `azure_endpoint` + `azure_deployment` parsed from a `/openai/deployments/{id}/responses` URL. The real DIAL Responses API is invoked against a base URL (`.../openai/v1/`) with the model passed in the request payload (`model=`), per `res.py`. A real run failed with `cannot extract deployment from URL path: /openai/v1/responses`. This escaped the earlier reviews because the tests mocked `AsyncAzureOpenAI` and used a fabricated `/openai/deployments/gpt-5.6/responses` URL that did not reflect the actual API contract — a reminder that mocked SDK boundaries can mask a wrong external contract.
- **Resolution note (verified)**:
  - `src/lib/utils/dial.py` — added `responses_base_url(url)` which strips a trailing `responses` segment, normalizes to a single trailing slash (required by the SDK when joining the relative `responses` path), and raises `ValueError("invalid responses URL")` when scheme/netloc is missing. Covered by `TestResponsesBaseUrl` (strip, no-segment, trailing-slash, invalid cases).
  - `src/worker/executors/responses.py:24-29` — now constructs `AsyncAzureOpenAI(base_url=responses_base_url(properties.url), ...)` with **no** `azure_deployment`; model flows via `payload.model` into `responses.stream(**kwargs)`. Verified by `test_base_url_derived_from_url` and `test_model_passed_in_payload_not_as_deployment` (asserts `azure_deployment` absent and `model` present in the stream call).
  - `src/lib/schemas/schedule.py:120-121` — `responses` now requires `payload.model`, raising `payload.model is required for target_type 'responses'`. Covered by `test_responses_requires_model_in_payload` and the integration `RESPONSES_REQUEST` (URL `.../openai/v1/responses`, payload includes `model`).
  - `contracts/responses-executor.md` and `documentation/properties.md` updated to describe the base-URL + `payload.model` approach.

### C-regression check — chat-completion path unregressed — Status: Confirmed
- `extract_deployment_id` and `parse_deployment` are reverted to chat-completion-only behavior (single `/chat/completions` suffix; multi-segment application ids preserved). `TestExtractDeploymentId` and `TestParseDeployment` pass, and `test_chat_completion.py` is green. The Responses fix is fully isolated to the new `responses_base_url` helper, so no shared parser used by chat completion was altered.

## Improvements

### I1 — Multi-segment application deployment id handling — Status: Resolved (superseded by C1)
- **File**: `src/lib/utils/dial.py` — `parse_deployment` / `extract_deployment_id`
- **Original problem**: `parse_deployment` truncated multi-segment application ids for both executors.
- **Resolution note**: `parse_deployment` delegates deployment-id extraction to `extract_deployment_id`, which preserves multi-segment application ids (`TestParseDeployment::test_application_deployment_multi_segment_preserved`). Note the interim `/responses` suffix handling added for this finding was **correctly reverted** under C1 — the Responses executor no longer uses `parse_deployment`/`extract_deployment_id` at all (it uses the new `responses_base_url` + `payload.model`), so `extract_deployment_id` is once again chat-completion-only, as originally intended. The multi-segment fix for the chat-completion path remains and is verified.

### I2 — Two overlapping deployment-URL parsers — Status: Resolved
- **File**: `src/lib/utils/dial.py` — `parse_deployment` (18) and `extract_deployment_id` (33)
- **Resolution note**: `parse_deployment` assembles only the endpoint locally and delegates deployment-id extraction to `extract_deployment_id` (`dial.py:30`), keeping a single source of truth for the chat-completion deployment id. The Responses path uses a distinct `responses_base_url` helper (different contract, no overlap), so no duplicated parsing logic remains.

## Minor Suggestions

- **M1 — Executor duplication is intentional but total.** Status: Open (by design). `responses.py` and `chat_completion.py` are now nearly byte-identical apart from `.responses.stream()` / `get_final_response()` vs `.chat.completions.stream()` / `get_final_completion()`. The plan explicitly chose "one executor mirroring the existing one" over an abstraction (YAGNI/Simplicity, Constitution V), so this is the sanctioned approach and I do **not** recommend refactoring now. Flagging only so it is a conscious decision: if a third OpenAI-style executor ever appears, extract a shared base template at that point (rule of three).
- **M2 — Untracked helper files at repo root.** Status: Closed (won't fix). `res.py`, `documentation/deployment/ihor-test.json`, and `../../../../AppData/Roaming/JetBrains/PyCharm2026.2/scratches/scheduler/scheduler-app-definition.json` are pre-existing untracked files, intentionally not part of this feature's deliverables per the coordinator. No action needed.
- **M3 — e2e file naming.** `tests/e2e/1.4 Create-responses-schedule-oauth.http` mirrors the existing `1.x` chat-completion files well. The `model` field is duplicated in the payload (`gpt-4.1-mini-2025-04-14`) in addition to the deployment in the URL; this matches `res.py`'s `responses.create(model=..., ...)` usage, so it is fine — just noting the payload `model` is what the SDK actually sends.

## Intent Alignment

The implementation matches the spec and plan closely:

- **FR-001 / FR-008** — `RESPONSES = "responses"` added to `TargetType` (`enums.py:25`); auto-reflected in OpenAPI and echoed in read responses (integration test asserts `target_type == "responses"`). ✅
- **FR-002 / FR-003 / SC-002 / SC-003** — `ResponsesExecutor` invokes the Responses API against `responses_base_url(properties.url)` with the model carried in `payload.model` (matching `res.py`), using `responses.stream()` + `get_final_response()` and returning `{"result": response.model_dump()}` for both transports; unit test `test_stream_and_non_stream_produce_same_result_shape` proves shape parity. ✅ (Corrected under C1.)
- **FR-004 / FR-005 / FR-007 / SC-004 / SC-005** — No changes to `retry.py`/`jobs.py`; retry-eligibility and timeout parity verified via `execute_with_policy` tests (`test_retryable_status_is_retried_then_succeeds`, `test_non_retryable_status_is_terminal`, `test_effective_timeout_passed_through_when_set`). OBO auth reuses `strip_bearer_prefix`. ✅
- **FR-006 / FR-009 / SC-001 / SC-006** — Validation branch in `schedule.py:113` correctly groups `RESPONSES` with `CHAT_COMPLETION` (no `method`, `api_version` required, default `stream=False`) while keeping `create_conversation` chat-completion-only (rejected for `responses`). Additionally, `responses` requires `payload.model` (`schedule.py:120-121`) — a `responses`-specific rule needed because the model is not carried in the URL. Covered by both unit and integration tests. ✅
- **FR-010** — Executor logs `event_type=getattr(stream_event, "type", None)` only; `test_obo_secret_not_in_logs` and `test_stream_events_log_event_type_only` confirm no content/credentials leak. ✅
- **No DB migration / no `api/` changes** — Confirmed; only enum, validation, executor, dispatcher, docs, tests touched. ✅

No divergence from stated intent was found. Note the create-conversation error message is hardcoded to `'chat_completion'` (`schedule.py:121,128`) which is correct and user-accurate, while the method/api_version messages were parameterized with `{self.target_type}` — a nice touch for clearer `responses` errors.

## Checklist Pass

- **Correctness & Logic** — PASS. Error mapping (`APIStatusError` → status code; generic → none) mirrors chat completion exactly; `stream` popped from kwargs; timeout applied only when set.
- **Type Safety** — PASS. Full type hints; `mypy` clean on 77 source files. `create_kwargs: dict[str, Any]` explicit.
- **Style & Conventions** — PASS. `ruff check` clean; uses `src.lib.utils.logging.logger`, no `print()`, no `assert`, Google-style docstrings on the new shared helper.
- **Architecture & Design** — PASS. Executor implements the `Executor` protocol; registered via dispatcher map; `HttpClientProvider` reused (no globals); `lib/` imports nothing from `worker/`. (See I2 for a soft DRY note.)
- **Database & ORM** — PASS (N/A). No model or migration changes; target props ride existing JSONB `kwargs`.
- **Project Structure** — PASS. New executor in `src/worker/executors/`, shared helper in `src/lib/utils/`, tests mirror `src/`.
- **Testing** — PASS. New unit tests (happy path incl. `base_url`/`model`/no-`azure_deployment`, failure path, policy parity, timeout), `TestResponsesBaseUrl`, dispatcher test, schema tests (incl. `payload.model` required), and integration `TestCreateResponsesSchedule` (201 + negatives). 102 targeted tests green in the re-verified scope. Note: one pre-existing unrelated failure (`test_exhausted_retries_persists_error_only`) reported by the coordinator is outside this feature's scope.
- **Security / Observability** — PASS. No secrets or streamed content logged; input validated via Pydantic at the API boundary; secret held as `SecretStr`.

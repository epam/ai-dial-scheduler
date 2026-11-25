# Research: Conversation ID Header and Scheduler Request Identity

All decisions below were either agreed with the user during discovery or verified against the code and the development
environment (Grafana Loki/Tempo, ADAS ClickHouse). No open `NEEDS CLARIFICATION` items remain.

## R1. Header name and value format

**Decision**: Send `X-CONVERSATION-ID` with the full conversation id, prefix included:
`conversations/{bucket}/.scheduler/{schedule_id}/[{deployment folder}/]{deployment}__{display name}__{run id}` — the exact
value stored in `schedule_runs.conversation_id`.

**Rationale** (observed on dev, 2026-11-25):
- DIAL Core maps the header to the span attribute `gen_ai.conversation.id` and to `dial_usage_log.chat_id`. A schedule whose
  owner put `X-CONVERSATION-ID: gemini-3.8-flash-response-latency` into `extra_headers` shows exactly that value in both.
- ADAS keys sessions on it: `usage_client_identity.client_session_id = chat_id`, `client_session_source = 'chat_id'`.
- When a scheduler-created conversation is continued in DIAL Chat (`ai-dial-chat/1.4.0-dev.139`), the recorded `chat_id` is
  the full `conversations/…/.scheduler/…` id, i.e. identical to `schedule_runs.conversation_id`. Sending the same value makes
  the scheduler call and the continuation one session.

**Alternatives considered**:
- Leaf only (`{deployment}__{display name}__{run id}`): no bucket lookup needed, but the run and its DIAL Chat continuation end
  up in different sessions, and an exact filter on `runs.conversation_id` does not find the scheduler call. Rejected by user.
- `run_id` / `schedule_id`: same session-split problem; `schedule_id` also merges all runs into one session (seen on dev with the
  constant manual value). Rejected.

## R2. Encoding of the header value

**Decision**: `urllib.parse.quote(value, safe=<all printable ASCII 0x20–0x7E>)` — every non-printable-ASCII character
(non-ASCII and control characters 0x00–0x1F, 0x7F) becomes percent-encoded UTF-8 bytes; printable ASCII, including space,
`/` and `%`, is kept.

**Rationale**:
- httpx encodes header values with `value.encode("ascii")` (`httpx/_models.py:_normalize_header_value`). A Cyrillic display
  name raises `UnicodeEncodeError`, a `ValueError` subclass, which `ChatCompletionExecutor` catches and converts into a
  `ScheduleRunError` → the run would fail. Control characters are rejected by h11 as invalid header bytes.
- `display_name` is validated only for non-emptiness, so any text can reach the header.
- `%`, `/` and `\` inside name parts are already escaped by `encode_name_part` (`%25`, `%2F`, `%5C`), so keeping `%` literal is
  unambiguous and, for ASCII names, header == stored id byte for byte.
- DIAL Chat does the same for chat-native ids on dev: `€` → `%E2%82%AC`, spaces raw.

**Alternatives considered**:
- `quote(value)` with default safe set: encodes spaces and `%`, so ASCII names would no longer equal the stored id. Rejected.
- RFC 8187 / base64: not understood by DIAL Core/ADAS as a plain id. Rejected.

**Downstream form**: DIAL Core stores the header value as received, so for non-ASCII names `chat.id` (and the DIAL Admin
session id) is the encoded string, while `schedule_runs.conversation_id` keeps the raw id. Filtering DIAL Admin for such a run
uses the encoded form; for ASCII names both forms are identical.

**Open (non-blocking)**: whether DIAL Chat encodes a *scheduler-created* non-ASCII id identically on continuation — checked
after deployment (quickstart §4.5).

## R2a. Header length limit

**Decision**: If the encoded id is longer than 4096 bytes (`MAX_CONVERSATION_ID_HEADER_BYTES`), `resolve_conversation_location`
raises `ConversationCreationError`, which follows the R5 failure path (no header, one warning, no conversation saved).

**Rationale**: `display_name` has no `max_length`, and percent-encoding grows a Cyrillic letter to 12 bytes. Proxies and DIAL
Core reject header sets above roughly 8 KiB with 400/431, which would turn a previously successful run into a failed one (SC-004).
4096 bytes leaves room for the other request headers and is far above any realistic id. Raised during code review.

**Alternatives considered**:
- Truncating the id: the header would no longer match the saved conversation, splitting the session. Rejected.
- Sending the header but still saving the conversation: the conversation would land in a session that has no scheduler call,
  which is what the feature exists to prevent. Rejected.
- Adding `max_length` to `display_name`: an API contract change affecting existing schedules; out of scope.

## R3. When and where the id is resolved

**Decision**: Resolve a `ConversationLocation` once per run in `execute_run`, after OBO credentials and before
`execute_with_policy`, only for `chat_completion` + `create_conversation`. Store it on `RunContext.conversation_location`.
The chat completion executor derives the header from it; `create_run_conversation` reuses it after the run.

**Rationale**:
- The id depends on the bucket (`GET /v1/bucket` with OBO credentials), the display name (DB), the deployment from the payload,
  schedule id and run id — none of which change between attempts, so resolving once guarantees FR-003 (same id per attempt)
  and FR-010 (saved location == header).
- `RunContext` is already the per-run state threaded through orchestrator and executors (it carries `effective_timeout`), so
  no new parameters are added to executor or helper signatures (constitution V: ≤ 3 parameters).
- On-demand runs call the same `execute_run`, so FR-003/US1-5 need no API change.
- The application reference lookup (`get_application`) is only needed for the document's `model` field, so it stays post-run
  and does not add pre-request latency.

**Alternatives considered**:
- httpx request event hook + contextvar: one place for all requests, but hidden control flow and it would fire for DialClient
  and REST/SSE calls too. Rejected (constitution V: "framework magic that obscures control flow is discouraged").
- Passing the id as an extra executor argument: changes the `Executor` protocol for one target type. Rejected.

## R4. Display name source

**Decision**: `resolve_conversation_location` loads the schedule itself (as `create_run_conversation` does today), instead of
changing `_resolve_credentials` to return the schedule.

**Rationale**: Query count per conversation run is unchanged (the lookup moves from post-run to pre-run), `_resolve_credentials`
and its tests stay untouched, and the location resolver remains self-contained. A deleted schedule surfaces as a
`ConversationCreationError` → warning → no header (spec edge case).

**Alternatives considered**: return `(credentials, schedule)` from `_resolve_credentials` — saves one indexed query but widens a
function used by every run type and touches many tests. Deferred (not needed now).

## R5. Failure semantics

**Decision**: Any exception while resolving the location (bucket error/missing bucket, invalid `payload.model`, schedule
missing, encoded id over 4096 bytes (R2a), unexpected error) → `logger.warning("conversation_id_resolve_failed", run_id,
schedule_id, reason)`, location stays
`None`, header omitted. After a successful run with `create_conversation` and no location →
`logger.info("conversation_create_skipped", run_id, schedule_id, reason="conversation_id_unresolved")`; no PUT; the run's
`conversation_id` stays `NULL`. No second bucket attempt.

**Rationale**: Spec FR-007..FR-009; observability must not reduce reliability (constitution I). Retrying the bucket after the
run would create a conversation whose session differs from the run call — exactly what the feature removes. The broad catch
mirrors `_maybe_create_conversation`, which already treats conversation work as best-effort. Exception messages from
`DialClient.get_bucket` carry only status/transport text, never tokens.

## R6. Precedence over caller headers

**Decision**: When the scheduler sends the header, drop every `extra_headers` key equal to `X-CONVERSATION-ID`
case-insensitively, then add the scheduler's value. When it does not, pass `extra_headers` through unchanged.

**Rationale**: User decision ("наш пріоритетніший"). Case-insensitive removal is required because httpx keeps differently-cased
duplicates as separate header lines (same reasoning as `strip_user_agent`).

## R7. User-Agent (commit `f94816b`)

**Decision**: Keep as implemented: `create_http_client()` sets `User-Agent: ai-dial-scheduler/<version>` on the shared API and
worker clients; `AsyncAzureOpenAI(default_headers=…)` in chat completion/responses executors; `strip_user_agent()` on
`extra_headers` for all executors; version read from `pyproject.toml` (project not installed as a package).

**Findings**:
- Before the change, Tempo showed `AsyncAzureOpenAI/Python 1.109.1` (chat/responses) and `python-httpx/0.28.1` (DialClient,
  REST, SSE). The OpenAI SDK sets its own `User-Agent` per request, so client-level headers alone are not enough — handled.
- Development images are built without version stamping → `ai-dial-scheduler/0.0.0` on dev. Acceptable for filtering.
- ADAS classifies `client_type` from the user agent and falls back to `chat` when a `chat_id` is present. A dedicated
  `scheduler` client type needs a classifier rule outside this repository (external follow-up).
- The MCP backing client only calls the scheduler's own API in-process; no User-Agent needed.

## R8. Log and trace correlation

**Decision**: No change to the logging pipeline. Bind `conversation_id` with `structlog.contextvars.bind_contextvars` right
after the location is resolved; add a unit test that the new warning carries `trace_id`/`span_id` inside an active span.

**Findings (dev)**:
- `_add_trace_span_ids` (`src/lib/utils/logging.py`) injects `trace_id`/`span_id`; worker and API stdout logs in Loki carry
  them (e.g. `credentials_fetch_failed`, `run_recorded_error`), OTEL Loki carries `traceid`/`spanid`.
- Trace context propagates worker → dial-core → adapters (`traceparent` via HTTPX instrumentation).
- Records logged outside a span (APScheduler's `Job … raised an exception`) have `otelTraceID: 0` — expected.
- Every record is exported twice to OTEL Loki (dict body + stringified body): two OTel logging handlers in the deployment.
  Out of scope (deployment configuration), recorded as a follow-up.
- `execute_run` and both callers already clear contextvars at the end of a run, so the bound `conversation_id` cannot leak
  into the next run.

# Feature Specification: Conversation ID Header and Scheduler Request Identity

**Feature Branch**: `feature/scheduler-user-agent` (shared with the User-Agent change, commit `f94816b`)

**Created**: 2026-11-25

**Status**: Draft

**Input**: User description: "Send X-CONVERSATION-ID header with the full DIAL conversation id on chat_completion runs with create_conversation=true, plus scheduler User-Agent (already implemented in commit f94816b on this branch) and log/trace verification."

## Background

DIAL Admin analytics groups model calls into **sessions** by the conversation identifier a client sends with each request
(`X-CONVERSATION-ID`). When such an identifier is present, the session is keyed on it; without it the call has no session.

The scheduler already saves each successful chat completion run as a DIAL conversation (when the schedule opts in with
`create_conversation`) and records its identifier on the run (`conversation_id`). When a user later opens that conversation
in DIAL Chat and continues it, DIAL Chat sends exactly that identifier
(`conversations/{bucket}/.scheduler/{schedule_id}/[{deployment folder}/]{deployment}__{display name}__{run id}`).

Today the scheduler's own call — the one that produced the conversation — carries no conversation identifier and is reported
with a generic SDK client name. An administrator therefore cannot find the scheduler's call by conversation, and the run and
its later continuation in DIAL Chat are not linked.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Find a scheduled run's call in DIAL Admin by conversation (Priority: P1)

An administrator investigates a scheduled run (cost, latency, a wrong answer). They take the run's `conversation_id` from the
scheduler API and filter DIAL Admin sessions by it. They see the model call the scheduler made for that run, and — if the
owner continued the conversation in DIAL Chat — the follow-up calls in the same session.

**Why this priority**: This is the purpose of the feature. Without it, scheduler traffic cannot be tied to a run or to a
conversation in DIAL Admin.

**Independent Test**: Run a chat completion schedule with `create_conversation: true`, read the run's `conversation_id`, and
verify that DIAL Core recorded the scheduler's call with exactly that conversation identifier.

**Acceptance Scenarios**:

1. **Given** a chat completion schedule with `create_conversation: true` and an ASCII display name, **When** a run executes
   and succeeds, **Then** the request sent to the target carries `X-CONVERSATION-ID` equal, character for character, to the
   `conversation_id` later recorded on the run.
2. **Given** that run, **When** the owner opens the conversation in DIAL Chat and sends a follow-up message, **Then** DIAL
   Admin shows the scheduler's call and the follow-up in one session.
3. **Given** a run whose target fails (non-2xx or timeout), **When** the request was sent, **Then** it still carried the
   conversation identifier, so the failed call is also findable in DIAL Admin (no conversation is saved, as today).
4. **Given** a schedule whose target is retried, **When** several attempts are made within one run, **Then** every attempt
   carries the same conversation identifier.
5. **Given** an on-demand run (triggered through the API rather than the worker), **When** it executes, **Then** it carries
   the header under the same rules as a scheduled run.

---

### User Story 2 - A missing bucket never breaks a run (Priority: P1)

The conversation identifier contains the owner's storage bucket, which must be looked up from DIAL before the target request.
If that lookup fails, the run must proceed exactly as it would without this feature, and operators must be able to see why the
header was not sent.

**Why this priority**: Observability must never reduce reliability. A run that succeeded before this feature must not fail
because of it.

**Independent Test**: Make the bucket lookup fail for a chat completion schedule with `create_conversation: true`, and verify
the target request is sent without the header, the run's outcome is unchanged, a warning is logged, and no conversation is
saved.

**Acceptance Scenarios**:

1. **Given** the bucket lookup fails (error status, transport error, or a response without a bucket), **When** the run
   executes, **Then** the target request is sent **without** an `X-CONVERSATION-ID` header (the header is omitted, not sent
   empty).
2. **Given** that failure, **When** it happens, **Then** a warning log entry `conversation_id_resolve_failed` is emitted with
   the run id, schedule id and a reason, and without any credential or token value.
3. **Given** that failure and a successful target response, **When** the run completes, **Then** the run is recorded as
   successful, no conversation is saved, an informational log entry states that conversation creation was skipped because the
   identifier could not be resolved, and the run's `conversation_id` stays empty.
4. **Given** the schedule's payload has no usable model/deployment value, **When** the run executes, **Then** it behaves as in
   scenarios 1–3 (no header, warning, no conversation).

---

### User Story 3 - Non-ASCII display names are safe (Priority: P1)

Schedule display names are free text and may be written in Cyrillic, contain emoji, or (by mistake) control characters. The
conversation identifier includes the display name, and request headers can only carry plain ASCII. A schedule named
"Щоденний звіт" must run as reliably as one named "Daily report".

**Why this priority**: Without safe encoding, a schedule with a non-English name would fail on every run — a regression that
would be introduced by this feature.

**Independent Test**: Run schedules whose display names contain Cyrillic, emoji, a literal `%`, and a newline, and verify each
run succeeds and the header contains only printable ASCII.

**Acceptance Scenarios**:

1. **Given** a display name containing non-ASCII characters, **When** the run executes, **Then** the header carries the
   identifier with every non-ASCII character percent-encoded as its UTF-8 bytes (e.g. `Щ` → `%D0%A9`), and the run succeeds.
2. **Given** a display name containing control characters (e.g. a newline), **When** the run executes, **Then** those
   characters are percent-encoded too, so no header can be split or injected.
3. **Given** an identifier made only of printable ASCII (including spaces, `/` and `%` escapes already present in the stored
   identifier), **When** the header is built, **Then** it is sent unchanged and equals the run's `conversation_id`.

---

### User Story 4 - Scheduler traffic is identifiable (Priority: P2)

An operator looking at DIAL Core logs, traces or analytics wants to tell scheduler traffic apart from other clients.

**Why this priority**: Already implemented on this branch (commit `f94816b`); it is included so the whole "request identity"
change is specified, verified and shipped together.

**Independent Test**: Run any schedule and verify every outbound request (DIAL Core calls and the target request, for all
target types) carries `User-Agent: ai-dial-scheduler/<version>`.

**Acceptance Scenarios**:

1. **Given** a run of any target type (chat completion, responses, REST, SSE), **When** requests are sent, **Then** each
   carries `User-Agent: ai-dial-scheduler/<version>`, including calls to DIAL Core (credentials, bucket, conversation save).
2. **Given** a schedule whose `extra_headers` contains a `User-Agent` in any casing, **When** it runs, **Then** the
   scheduler's value is sent and the caller's value is ignored.

---

### User Story 5 - Run logs and traces are correlated (Priority: P2)

An operator debugging a run in Grafana wants to jump between the run's logs, its trace, and its DIAL Admin session.

**Why this priority**: Trace and span identifiers are already present in run logs; this story keeps that guarantee for the new
log entries and adds the conversation identifier as a common key.

**Independent Test**: Execute a chat completion run with tracing enabled and verify that every log entry emitted during the run
(including `conversation_id_resolve_failed` when it happens) carries the trace id and span id, and — once resolved — the
conversation identifier.

**Acceptance Scenarios**:

1. **Given** tracing is enabled, **When** a run emits log entries inside its run span, **Then** each entry carries `trace_id`
   and `span_id`.
2. **Given** the conversation identifier was resolved, **When** later log entries of the same run are emitted, **Then** they
   carry `conversation_id`.

---

### Edge Cases

- **Caller already sets the header**: a schedule with `create_conversation: true` whose `extra_headers` contains
  `X-CONVERSATION-ID` (any casing) — the scheduler's value replaces it; exactly one header is sent.
- **Caller sets the header, scheduler does not**: a schedule with `create_conversation: false` (or the bucket lookup failed) —
  the caller's header is passed through unchanged; the scheduler adds nothing.
- **Other target types**: responses, REST and SSE runs, and chat completion runs without `create_conversation`, never get the
  header from the scheduler (a caller-supplied one passes through as today).
- **Application deployments**: a deployment id given in its percent-encoded URL form (`applications/{bucket}/Planning%20Assistant__0.0.1`)
  yields the same identifier as the conversation that is later saved (raw, decoded name), so the header and the saved
  conversation never disagree.
- **Application reference lookup fails after the run**: the header was already sent; conversation creation fails and is logged
  as today; the run outcome is unchanged.
- **Schedule deleted between credential lookup and the run**: unchanged behaviour; the header is only built from data already
  loaded for this run.
- **Retries**: the identifier is resolved once per run; a failing bucket lookup is not retried per attempt.
- **Very long identifier**: the display name has no length limit, and percent-encoding multiplies the size of non-ASCII
  characters (a Cyrillic letter becomes 6 bytes, a CJK character 9). An oversized header would make proxies or DIAL Core
  reject the request and turn a successful run into a failed one, so when the encoded identifier exceeds 4096 bytes only the
  header is omitted; the conversation is still saved, exactly as before this feature.
- **Slow bucket lookup**: resolution runs before the run's own timeout and retry policy apply, so it is bounded separately;
  if it does not finish in time the identifier is treated as unresolved.
- **Caller auth headers**: an `extra_headers` entry matching the OBO header name in a different casing (or, for SDK-based
  targets, `api-key` / `Authorization`) is dropped, so it can never be sent next to the run owner's credentials.
- **Non-DIAL targets**: a chat completion schedule may point at a third-party OpenAI-compatible endpoint; that endpoint then
  receives the header, which contains the owner's bucket id and the display name. This is documented rather than prevented.
- **Non-ASCII names downstream**: DIAL Core and DIAL Admin record the identifier exactly as received, i.e. in its encoded form.
  For ASCII names this equals the run's `conversation_id`; for non-ASCII names a DIAL Admin session is found by the encoded
  form of `conversation_id` (FR-006 applied to it), not by the raw value.

## Requirements *(mandatory)*

### Functional Requirements

**Conversation identifier header**

- **FR-001**: For runs of chat completion schedules with `create_conversation: true`, the system MUST resolve the run's
  conversation identifier **before** sending the target request, using the owner's bucket, the schedule's display name, the
  deployment from the payload, the schedule id and the run id.
- **FR-002**: The resolved identifier MUST be the same value that the saved conversation receives and that is recorded on the
  run as `conversation_id` (prefix `conversations/` included).
- **FR-003**: When the identifier is resolved, the target request MUST carry it in the `X-CONVERSATION-ID` header, on every
  attempt of the run.
- **FR-004**: The header MUST NOT be sent by the scheduler for responses, REST or SSE targets, nor for chat completion
  schedules with `create_conversation: false`.
- **FR-005**: When the scheduler sends the header, any caller-supplied `X-CONVERSATION-ID` in `extra_headers` (case-insensitive)
  MUST be replaced so exactly one value — the scheduler's — is sent. When the scheduler does not send it, caller-supplied
  values MUST pass through unchanged.
- **FR-006**: The header value MUST contain only printable ASCII: every character outside printable ASCII (non-ASCII and
  control characters) MUST be percent-encoded as its UTF-8 bytes; printable ASCII characters MUST be kept as they are.

**Failure handling**

- **FR-007**: If the identifier cannot be resolved (bucket lookup fails, returns no bucket or exceeds the resolution time
  bound, the payload has no usable deployment, or the schedule's display name is unavailable), the target request MUST be
  sent without the header, and the run MUST proceed and be recorded exactly as it would without this feature.
- **FR-007a**: If the identifier is resolved but its encoded form exceeds 4096 bytes, the target request MUST be sent without
  the header, a warning `conversation_id_header_omitted` MUST be logged, and the conversation MUST still be saved.
- **FR-007b**: Resolving the identifier MUST be bounded in time (10 seconds), since it runs outside the run's timeout and retry
  policy, and MUST reuse the schedule row already loaded for the run instead of reading it again.
- **FR-008**: A resolution failure MUST emit one warning log entry `conversation_id_resolve_failed` per run, with run id,
  schedule id and reason, and MUST NOT log credentials or tokens.
- **FR-009**: After a resolution failure, the system MUST NOT attempt to save a conversation for that run; it MUST emit an
  informational log entry stating creation was skipped because the identifier was unresolved, and the run's `conversation_id`
  MUST stay empty.
- **FR-010**: Conversation saving after a successful run MUST reuse the identifier resolved before the request (no second
  bucket lookup), so the saved conversation's location always equals the header that was sent.

**Request identity (implemented in `f94816b`)**

- **FR-011**: Every outbound request of the scheduler (DIAL Core calls and target requests of all types, in both the worker and
  the API process) MUST carry `User-Agent: ai-dial-scheduler/<version>`, where the version is the released project version.
- **FR-012**: A `User-Agent` supplied in `extra_headers` (any casing) MUST be ignored.

**Observability**

- **FR-013**: Once resolved, the conversation identifier MUST be attached to all subsequent log entries of the run.
- **FR-014**: Log entries emitted during a run, including the new ones, MUST continue to carry `trace_id` and `span_id` when
  tracing is enabled.

**Documentation**

- **FR-015**: User-facing configuration documentation for `create_conversation` and `extra_headers` MUST describe the header,
  its precedence over caller values, its encoding, the 4096-byte limit, the resolution time bound, the behaviour when the
  identifier cannot be resolved, and that non-DIAL targets receive the header (with the owner's bucket id and display name).

**Request headers owned by the scheduler**

- **FR-016**: Caller-supplied `extra_headers` MUST NOT duplicate the run owner's auth header in any casing: entries matching the
  OBO header name (and, for chat completion and responses targets, `api-key` and `Authorization`) MUST be dropped before the
  request is sent.

### Key Entities

- **Conversation location**: the parts that determine where a run's conversation lives and what its identifier is — owner
  bucket, schedule id, deployment (folder and name), display name, run id. Resolved once per run, before the target request,
  and reused when the conversation is saved.
- **Run** (existing): gains no new stored field; its existing `conversation_id` is the value the header carries (in encoded
  form for non-ASCII names). It is stored raw; DIAL Core and DIAL Admin keep the encoded form they received, so the two are
  identical for ASCII names and differ only by percent-encoding for non-ASCII names.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For 100% of successful chat completion runs with `create_conversation: true` and an ASCII display name, the
  conversation identifier recorded by DIAL Core for the scheduler's call equals the run's `conversation_id`.
- **SC-002**: An administrator can locate a scheduled run's model call in DIAL Admin within one filter step, using only the
  run's `conversation_id` (in its encoded form for non-ASCII display names).
- **SC-003**: A run and its continuation in DIAL Chat appear as one session in DIAL Admin.
- **SC-004**: Zero runs fail because of this feature: run success rates for chat completion schedules before and after
  release are equal, including schedules with non-ASCII display names and runs where the bucket lookup fails.
- **SC-005**: 100% of outbound scheduler requests observed in DIAL Core traces/analytics report the scheduler as the client
  (`ai-dial-scheduler/<version>`).
- **SC-006**: Every bucket resolution failure is visible in logs as exactly one warning per affected run, carrying trace and
  span identifiers.

## Assumptions

- DIAL Core reads the conversation identifier from `X-CONVERSATION-ID` and DIAL Admin analytics keys sessions on it — observed
  on the development environment (the identifier appears as the session id with source `chat_id`).
- DIAL Chat percent-encodes non-ASCII characters of a conversation identifier as UTF-8 bytes and leaves spaces as they are
  (observed for chat-native conversations). Whether it does the same when continuing a scheduler-created conversation with a
  non-ASCII name is **not yet confirmed**; ASCII names are confirmed to match exactly. This is verified after deployment
  (see Verification) and does not block implementation.
- The bucket lookup uses the run owner's on-behalf-of credentials, as conversation creation does today; for the standard
  `dial-native` service this succeeds whenever the credential lookup succeeded. Moving the lookup before the request does not
  change its failure probability, only its timing.
- Development builds report version `0.0.0` in the User-Agent because the version is stamped only by the release workflow;
  this is acceptable for filtering.
- A dedicated "scheduler" client type in DIAL Admin requires a classifier rule in the analytics pipeline, owned outside this
  repository. Until it exists, scheduler calls carrying a conversation identifier are classified like DIAL Chat traffic; the
  User-Agent still distinguishes them.

### Out of Scope

- Sending the header for responses, REST or SSE targets, or for chat completion schedules without `create_conversation`.
- Storing the owner's bucket on the schedule to avoid the per-run lookup.
- Restricting the header to DIAL-hosted targets (documented instead).
- Changing the header encoding for display names that combine `%`, `/` or `\` with non-ASCII characters.
- Duplicated export of OpenTelemetry log records (deployment configuration, not application code).
- New span attributes on the run span.

## Verification (post-deployment, development environment)

1. **Traces**: on the ingress span of a scheduler call, the client user agent is `ai-dial-scheduler/<version>`; on the DIAL Core
   span, the conversation id attribute equals the run's `conversation_id`.
2. **Analytics**: for the run's calls, the recorded chat id equals the run's `conversation_id`, the session source is the chat
   id, and the client user agent is the scheduler's.
3. **Logs**: worker log entries for the run carry `trace_id`, `span_id` and `conversation_id`; no `conversation_id_resolve_failed`
   warnings appear under normal operation.
4. **DIAL Admin**: filtering sessions by the run's `conversation_id` shows the scheduler's call; after continuing the
   conversation in DIAL Chat, the follow-up appears in the same session.
5. **Non-ASCII**: repeat step 4 with a Cyrillic display name, filtering by the encoded form of `conversation_id`, and compare
   the identifier DIAL Chat sends on continuation with the one the scheduler sent.

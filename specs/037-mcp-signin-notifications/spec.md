# Feature Specification: Scheduler MCP Sign-In Status Signal

**Feature Branch**: `037-mcp-signin-notifications`

**Created**: 2026-08-28

**Status**: Draft

**Input**: User description: "When the scheduler MCP tool is invoked in a chat session, check whether the current user is signed in to the required external service. If not, the response must carry a structured sign-in-required signal so the DIAL Chat UI can surface its existing sign-in popup, instead of the assistant just describing the problem in prose. DIAL Core's client-channel protocol (`/v1/ops/client-channel/interact`, method `external-service/signin`) already exists and is confirmed working end-to-end against a live DIAL Core; this feature both produces the structured signal and — when the caller forwarded a client-channel id — actively pushes the popup itself, as the caller's own identity."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Assistant learns sign-in state before creating a schedule (Priority: P1)

A user asks the chat assistant to create a new schedule that will run against an external service (e.g. `dial-native`) on their behalf. Before attempting the action, the assistant checks whether the user is currently signed in to that service. If not, the assistant receives a structured result it can act on (rather than a plain sentence it can only repeat), and tells the user they need to sign in, naming the specific service.

**Why this priority**: This is the core of the feature — without a reliable, structured signal, no downstream popup integration is possible and the assistant has nothing better than guesswork to fall back on.

**Independent Test**: Call the `get_sign_in_status` MCP tool for a user known not to be signed in to a given service; verify the response is a structured object with a machine-readable "not signed in" state, the service identifier, and a human-readable reason — and that this works with no involvement from the schedule create/update flow.

**Acceptance Scenarios**:

1. **Given** a user who has never connected the `dial-native` service, **When** the assistant calls the sign-in status check for that service, **Then** the result reports "not signed in" with the service identifier and a reason a person can act on.
2. **Given** a user who is currently signed in to the service, **When** the assistant calls the sign-in status check, **Then** the result reports "signed in" with no error and no action required.
3. **Given** DIAL itself is unreachable while the check is performed, **When** the assistant calls the sign-in status check, **Then** the result distinguishes this outage case from "not signed in" so the assistant does not tell the user to sign in when signing in would not help.

---

### User Story 2 - Creating or updating a schedule is blocked until sign-in (Priority: P2)

A user asks the assistant to create or update a schedule for an external service they are not signed in to. The system refuses to create/update the schedule and returns a structured result identifying which service requires sign-in and why, so the assistant can relay a specific, actionable message instead of a generic failure.

**Why this priority**: Prevents schedules from being silently created against a service the user cannot actually authenticate to at run time, while building on the same structured signal from User Story 1.

**Independent Test**: Attempt to create (and separately, update) a schedule for a service the test user is not signed in to; verify the request is rejected with a structured payload naming the service and the required action, and that no schedule is created/modified as a result.

**Acceptance Scenarios**:

1. **Given** a user not signed in to the schedule's target service, **When** they ask the assistant to create a schedule for that service, **Then** the request is rejected, no schedule is created, and the rejection identifies the specific service and that sign-in is required.
2. **Given** a user not signed in to the schedule's target service, **When** they ask the assistant to update an existing schedule to use that service, **Then** the request is rejected the same way and the existing schedule is left unchanged.
3. **Given** DIAL is unreachable while the precondition is being checked, **When** a create or update is attempted, **Then** the request fails with an outage indication distinct from "sign-in required," and no schedule is created/modified.

---

### User Story 3 - The sign-in signal is shaped for a UI popup (Priority: P3)

The chat UI's existing sign-in popup mechanism (realized concretely in User Story 4) reads the
"not signed in" signal produced by this feature to prompt the user interactively. For that
integration to work without re-visiting dial-scheduler again, the signal this feature produces
must carry every piece of information that popup needs: which service, a stable identifier for
that service usable when asking the user to reconnect it, and a distinction between "you need
to sign in" and "we could not tell."

**Why this priority**: This is a payload-shape requirement, not new user-facing behavior by
itself — it shapes the payload from Stories 1 and 2 so the popup integration in User Story 4 is
additive rather than a breaking rework. It is lower priority because nothing observable changes
for today's users if it is done adequately versus perfectly; it mainly reduces rework.

**Independent Test**: Given only the documented shape of the sign-in-required payload,
independent of User Story 4's own popup-delivery mechanics, confirm a client could identify the
affected service and construct a sign-in prompt for it using only fields present in the payload.

**Acceptance Scenarios**:

1. **Given** the documented payload shape for a "sign-in required" result, **When** a hypothetical UI consumer inspects it, **Then** it can determine which external service is affected using a stable identifier, without parsing free-form text.
2. **Given** the documented payload shape, **When** a hypothetical UI consumer inspects a "not signed in" result versus a "could not determine" (outage) result, **Then** the two are distinguishable by a machine-readable field, not by the wording of a message.

---

### User Story 4 - Not-signed-in triggers an interactive popup when the chat session supports it (Priority: P2)

The chat client that called the MCP tool has an active client-channel subscription (it sent an
`X-DIAL-CLIENT-CHANNEL-ID` on the request). When the check finds the user not signed in, the
system pushes an `external-service/signin` prompt to that same chat session via DIAL Core's
client-channel protocol, using the caller's own identity (not the scheduler's), and waits
briefly for the user to respond. If the user completes sign-in, the check is re-run and the
original request proceeds as if the user had been signed in all along; if declined, denied, or
timed out, the request is rejected exactly as it would have been without a channel.

**Why this priority**: Turns User Stories 1/2's structured signal into an actual popup for
clients that support it, without requiring any dial-scheduler change on the client side —
purely additive to a client that has no channel.

**Independent Test**: With a client-channel subscribed and a user not signed in, call
create_schedule (or get_sign_in_status) with the channel id forwarded; verify an
`external-service/signin` event is delivered on the subscribed channel, and that reporting
`"success"` back makes the same request succeed without the caller retrying manually.

**Acceptance Scenarios**:

1. **Given** a client-channel id is present on the request and the user is not signed in,
   **When** the check runs, **Then** an `external-service/signin` event naming the affected
   service is delivered to that channel.
2. **Given** the popup is answered with `"success"`, **When** the sign-in check re-runs,
   **Then** it reports `signed_in` and (for create/update) the original action proceeds.
3. **Given** the popup is answered with `"denied"`, times out, or no channel id was present on
   the request at all, **When** the check completes, **Then** the result is exactly the
   `not_signed_in`/`unknown` outcome User Stories 1/2 already define — no new failure mode.
4. **Given** the popup attempt itself fails (DIAL Core unreachable, malformed response), **When**
   the check completes, **Then** the underlying `not_signed_in`/`unknown` state is still
   returned — a failed popup attempt is never promoted to a harder error.

### Edge Cases

- What happens when the user is signed in to some, but not all, of the external services a schedule's actions would touch? The precondition check is per target service; a schedule referencing multiple services must have every one of them satisfied, and the rejection must identify the first (or all) unmet services rather than an aggregate service-less refusal.
- What happens when the user's sign-in later expires or is revoked after a schedule was already created while they were signed in? Existing scheduled runs are not covered by this feature; the sign-in check only gates the create/update request itself. This is called out explicitly in Assumptions.
- How does the system behave if the "service" a schedule targets is not one that supports sign-in status checks at all (no external-service concept applies)? The check is skipped/passes trivially for such services, since a precondition on something that cannot be checked cannot be enforced.
- What happens if the same "not signed in" state is queried repeatedly in quick succession (e.g. the assistant checks status right before every action)? Each check is independent and reflects current state; no caching correctness requirement is implied by this feature.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a way for an assistant, via the MCP tool surface, to check whether the current user is currently signed in to a named external service, without side effects on scheduler state.
- **FR-002**: The sign-in check result MUST distinguish, via a machine-readable field (not only free text), at least these three states: signed in; not signed in; and unable to determine (e.g. because the check itself failed due to an outage).
- **FR-003**: When the result is "not signed in," it MUST include a stable identifier for the affected external service, sufficient for a future caller to know which service to prompt the user to connect, and a human-readable reason suitable for an assistant to relay verbatim.
- **FR-004**: When the result is "unable to determine," it MUST be clearly distinguishable from "not signed in" so that an assistant never tells a user to sign in when doing so would not resolve the underlying problem.
- **FR-005**: Creating a schedule MUST be rejected, with no schedule created, if the user is not signed in to the external service the schedule targets.
- **FR-006**: Updating a schedule MUST be rejected, with no changes applied, if the user is not signed in to the external service the updated schedule would target.
- **FR-007**: The rejection response for FR-005/FR-006 MUST carry the same machine-readable "not signed in" signal and service identifier described in FR-003, not only a prose message.
- **FR-008**: If the sign-in check cannot be completed due to an outage or transport failure, create/update requests MUST fail distinctly from the "not signed in" rejection (FR-004's distinction applied to the enforcement path), and MUST NOT create or modify a schedule.
- **FR-009**: The system MUST NOT expose, log, or otherwise surface the user's credentials, tokens, or any other sign-in secret at any point during the check or the enforcement path.
- **FR-010**: The sign-in-required signal's structure (fields and their meaning) MUST be documented as a stable contract that a future chat-UI/DIAL Core integration can build against, independent of the human-readable message text, which MAY change over time.
- **FR-011**: When the caller forwards a client-channel id (`X-DIAL-CLIENT-CHANNEL-ID`) and the
  check finds the user not signed in, the system MUST attempt to push an
  `external-service/signin` prompt to that channel via DIAL Core's client-channel protocol,
  using the caller's own forwarded identity (never the scheduler's own service identity — DIAL
  Core resolves the channel by the caller's bucket, which must match the subscribing chat
  session's). On success, the check MUST be re-run once before returning. A failed or absent
  popup attempt MUST NOT change or worsen the `not_signed_in`/`unknown` result FR-002 already
  defines — pushing the popup is best-effort and never fatal to the check itself.
- **FR-012**: The MCP tool surface MUST forward the caller's client-channel id (when present)
  to the underlying check the same way it already forwards the caller's Api-Key, and MUST NOT
  expose either as a manually-suppliable tool input parameter.

### Key Entities

- **Sign-In Status**: The outcome of checking one user's connection to one external service at a point in time. Carries: the service's stable identifier, a machine-readable state (signed in / not signed in / undetermined), and a human-readable reason. Not persisted — computed fresh on each check.
- **External Service**: A named third-party or platform capability (e.g. `dial-native`) that a schedule's actions may run against on the user's behalf, and to which the user's sign-in status is meaningful. Identified by a stable id used consistently across the status check, the enforcement rejection, and any future UI integration.
- **Client Channel**: An existing DIAL Core mechanism (not introduced by this feature) that lets a subscribed chat session receive interactive prompts mid-conversation. Identified by an opaque id the caller forwards on its request; this feature only reads and relays that id — it does not create, own, or manage channels.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An assistant can determine a user's sign-in state for a given external service, and receive a distinct answer for "signed in," "not signed in," and "could not determine," in 100% of check requests — never an ambiguous or purely textual answer.
- **SC-002**: Zero schedules are created or updated against an external service while the requesting user is not signed in to it, across all create/update attempts.
- **SC-003**: When a create/update is rejected for a sign-in reason, the affected service is unambiguously identifiable from the response's structured fields alone, without needing to parse the human-readable message, in 100% of such rejections.
- **SC-004**: An outage in the underlying sign-in check never results in the user being told to "sign in" when sign-in would not fix the problem; this distinction holds in 100% of outage cases exercised in testing.
- **SC-005**: A future engineer integrating the chat UI popup can identify, from this feature's documented payload contract alone, every field needed to construct that popup, without requiring further changes to dial-scheduler's response shape.
- **SC-006**: When a client-channel id is present and the user is not signed in, the popup event is delivered to that channel; when the popup is answered or fails, the caller's request completes with no observable hang beyond the configured wait, and never surfaces a failure mode other than `not_signed_in`/`unknown`.

## Assumptions

- The set of external services relevant to sign-in (e.g. `dial-native`) and how "signed in" is determined for each is defined by DIAL itself; this feature checks and relays that state rather than defining new sign-in mechanics.
- Popup delivery depends entirely on the caller (the MCP client/agent framework) actually forwarding a live client-channel id on its request; a caller that never subscribes to a channel gets the same text-only behavior User Stories 1/2 already provide, which remains useful on its own.
- DIAL Core's client-channel protocol and the chat-side dialog that answers `external-service/signin` prompts are pre-existing platform mechanisms this feature relies on but does not own, build, or modify.
- A schedule created while the user was signed in is not retroactively invalidated if the user's sign-in later lapses; ongoing run behavior for already-created schedules is unaffected by this feature.
- Services that have no notion of "sign-in" (if any exist in the schedule's target set) are not subject to this precondition.
- No new persisted data is introduced; sign-in state is checked live against DIAL on each request rather than cached or stored by the scheduler.

# Contract: Outbound Request Headers

The scheduler exposes no new REST endpoint or schema. This contract defines the headers the scheduler adds to requests it
sends (to DIAL Core and to schedule targets), which DIAL Core logs/traces and DIAL Admin analytics rely on.

## `User-Agent` (since commit `f94816b`)

| Applies to                                                                 | Value                              |
|----------------------------------------------------------------------------|------------------------------------|
| Every outbound request: OBO credentials, bucket, application details, conversation PUT, and target requests of all types (`chat_completion`, `responses`, `rest`, `sse`), from both the worker and the API process | `ai-dial-scheduler/<version>` (`<version>` = `project.version` from `pyproject.toml`; bare `ai-dial-scheduler` if unreadable) |

- A `User-Agent` key in `properties.extra_headers` (any casing) is ignored.

## `X-CONVERSATION-ID` (this feature)

| Target type / condition                                             | Header sent by the scheduler                     |
|---------------------------------------------------------------------|--------------------------------------------------|
| `chat_completion` with `create_conversation: true`, id resolved     | Yes — encoded conversation id, every attempt     |
| `chat_completion` with `create_conversation: true`, id NOT resolved | No (omitted, never empty)                        |
| `chat_completion` with `create_conversation: false`                 | No                                               |
| `responses`, `rest`, `sse`                                          | No                                               |
| DIAL Core metadata calls (OBO credentials, bucket, application, PUT)| No                                               |

**Value**: `encode(conversation_id)` where `conversation_id` is
`conversations/{bucket}/.scheduler/{schedule_id}/[{deployment folder}/]{deployment}__{display name}__{run id}` — identical to the
run's `conversation_id` in the scheduler API (`GET /v1/schedules/{id}/runs/{run_id}`).

**Encoding**: each character outside printable ASCII (U+0020–U+007E) is replaced by the percent-encoding of its UTF-8 bytes;
printable ASCII (including space, `/`, `%`) is kept as is.

| Display name       | Header value fragment                     |
|--------------------|-------------------------------------------|
| `Daily report`     | `…__Daily report__<run id>`               |
| `Щоденний звіт`    | `…__%D0%A9%D0%BE%D0%B4%D0%B5%D0%BD%D0%BD%D0%B8%D0%B9 %D0%B7%D0%B2%D1%96%D1%82__<run id>` (space kept) |
| `50% done`         | `…__50%25 done__<run id>` (`%` escaped by name-part encoding, kept literal by header encoding) |
| `line1\nline2`     | `…__line1%0Aline2__<run id>`              |

**Length limit**: if the encoded value would exceed 4096 bytes (`MAX_CONVERSATION_ID_HEADER_BYTES`), the id is treated as not
resolved: the header is omitted, `conversation_id_resolve_failed` is logged, and no conversation is saved for the run. This keeps
an unbounded display name from producing a header set that proxies or DIAL Core reject.

**Precedence**: when the scheduler sends the header, any `extra_headers` key matching `X-CONVERSATION-ID` case-insensitively is
removed first, so exactly one value is sent. When the scheduler does not send it, caller-supplied values pass through unchanged.

**Downstream mapping (DIAL, observed)**: DIAL Core span attribute `gen_ai.conversation.id`; ADAS `dial_usage_log.chat_id`;
`usage_client_identity.client_session_id` with `client_session_source = 'chat_id'`.

**Downstream form**: DIAL records the header value as received, i.e. encoded. For ASCII display names it equals the run's
`conversation_id`; for non-ASCII names DIAL Admin sessions carry the encoded form, so filtering uses
`encode(conversation_id)`, not the raw value returned by the scheduler API.

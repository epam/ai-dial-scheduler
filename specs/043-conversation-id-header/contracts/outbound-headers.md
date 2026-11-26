# Contract: Outbound Request Headers

The scheduler exposes no new REST endpoint or schema. This contract defines the headers the scheduler adds to requests it
sends (to DIAL Core and to schedule targets), which DIAL Core logs/traces and DIAL Admin analytics rely on.

## `User-Agent` (since commit `f94816b`)

| Applies to                                                                 | Value                              |
|----------------------------------------------------------------------------|------------------------------------|
| Every outbound request: OBO credentials, bucket, application details, conversation PUT, and target requests of all types (`chat_completion`, `responses`, `rest`, `sse`), from both the worker and the API process | `ai-dial-scheduler/<version>` (`<version>` = `project.version` from `pyproject.toml`; bare `ai-dial-scheduler` if unreadable) |

- A `User-Agent` key in `properties.extra_headers` (any casing) is ignored.

## Auth headers in `extra_headers`

| Target type                     | Caller entries dropped (any casing)                               |
|---------------------------------|-------------------------------------------------------------------|
| `rest`, `sse`                   | the OBO header name (e.g. `Authorization`, `Api-Key`), `User-Agent` |
| `chat_completion`, `responses`  | the OBO header name, `api-key`, `Authorization`, `User-Agent`      |

The OBO value is therefore sent exactly once and a caller cannot add a second identity.

## `X-CONVERSATION-ID` (this feature)

| Target type / condition                                             | Header sent by the scheduler                     |
|---------------------------------------------------------------------|--------------------------------------------------|
| `chat_completion` with `create_conversation: true`, id resolved     | Yes — encoded conversation id, every attempt     |
| `chat_completion` with `create_conversation: true`, id NOT resolved | No (omitted, never empty)                        |
| `chat_completion` with `create_conversation: true`, encoded id > 4096 bytes | No (conversation still saved)            |
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

**Length limit**: if the encoded value would exceed 4096 bytes (`MAX_CONVERSATION_ID_HEADER_BYTES`), only the header is omitted:
`conversation_id_header_omitted` is logged and the conversation is still saved. This keeps an unbounded display name from
producing a header set that proxies or DIAL Core reject, without losing the conversation.

**Resolution bound**: the id is resolved before the run (bucket lookup) within 10 seconds
(`CONVERSATION_LOCATION_TIMEOUT_SECONDS`); a timeout counts as "id NOT resolved".

**Recipients**: the header is sent to the schedule's `url` whatever it points at, including non-DIAL OpenAI-compatible
endpoints, which then receive the owner's bucket id and the display name.

**Precedence**: when the scheduler sends the header, any `extra_headers` key matching `X-CONVERSATION-ID` case-insensitively is
removed first, so exactly one value is sent. When the scheduler does not send it, caller-supplied values pass through unchanged.

**Downstream mapping (DIAL, observed)**: DIAL Core span attribute `gen_ai.conversation.id`; ADAS `dial_usage_log.chat_id`;
`usage_client_identity.client_session_id` with `client_session_source = 'chat_id'`.

**Downstream form**: DIAL records the header value as received, i.e. encoded. For ASCII display names it equals the run's
`conversation_id`; for non-ASCII names DIAL Admin sessions carry the encoded form, so filtering uses
`encode(conversation_id)`, not the raw value returned by the scheduler API.

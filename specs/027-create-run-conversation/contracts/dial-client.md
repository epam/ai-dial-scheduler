# Contract: New `DialClient` methods

Extends `src/lib/clients/dial_client.py`. All calls use the run owner's OBO token as the `Api-Key` header and reuse the shared httpx client + configured timeout, mirroring the existing `get_obo_credentials` / `get_user_info` pattern. Errors are typed and never carry issued secrets.

Base URL: `settings.dial_url`. Verified against a DIAL Core dev instance.

## `get_bucket(api_key: str) -> str`

- **HTTP**: `GET {base_url}/v1/bucket`
- **Auth**: `Api-Key: <owner OBO token>`
- **200 body**: `{ "bucket": "<bucket-id>" }` → returns the `bucket` string.
- **Verified**: returns `{"bucket": "9p6N8pJg8BRJCsCs9uvb6mzwCRAe6NBJfDva5ckebXWHjirHvHYSk5MUHCzb5xjSRF"}`.
- **Errors**: non-2xx / transport / missing `bucket` → `ConversationCreationError` (caught upstream; run stays success).

## `get_application(api_key: str, application_id: str) -> ApplicationDetails`

- **HTTP**: `GET {base_url}/v1/applications/{application_id}` (application_id URL-encoded; the id begins with `applications/`).
- **Auth**: `Api-Key: <owner OBO token>`
- **200 body**: application object including `"reference": "<model-ref>"` → parsed into `ApplicationDetails(reference=...)`.
- **Verified**: `GET /openai/applications/applications/9p6N8.../Untitled%20app%201101__0.0.1` → `"reference": "c9e5decf-1284-421e-ab84-c790beea837d"`.
- **Errors**: non-2xx / transport / missing `reference` → `ConversationCreationError`.
- **Only called** when the deployment id is an application (`applications/` prefix).

## `put_conversation(api_key: str, bucket: str, conversation: DialConversation) -> None`

- **HTTP**: `PUT {base_url}/v1/conversations/{bucket}/{conversation_path}`
  - `conversation_path = .scheduler/{schedule_id}/{run_id}`
  - Full resource id = `conversations/{bucket}/.scheduler/{schedule_id}/{run_id}` (matches `DialConversation.id`).
- **Auth**: `Api-Key: <owner OBO token>`
- **Body**: `conversation.model_dump()` (JSON). Field names match DIAL Chat (`model`, `messages`, `folderId`, `updatedAt`, `responseFormat`, ...).
- **Success**: 2xx → returns `None`.
- **Errors**: non-2xx / transport → `ConversationCreationError`.
- **⚠ Mutating call**: In production the worker performs this automatically with the owner's OBO credential. During manual investigation via the `dial-api` skill it requires explicit user approval (`--confirm`).

## Error type

`ConversationCreationError` (new, in `src/lib/exceptions/errors.py`): raised by these methods and the builder; caught at the `jobs.py` boundary, logged as a structured error, never re-raised past run success. Carries status/reason for diagnosis, never a token or streamed content.

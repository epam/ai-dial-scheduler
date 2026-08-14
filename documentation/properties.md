API field `properties`, reflected by `schedules.kwargs` and equal to the actual
kwargs passed to `scheduler.add_job()`.

Represents the task run configuration and has the following structure:
{
    "target_type": "chat_completion|responses|rest|sse",
    "method": "POST|PUT|GET|null",
    "url": "http://example.com/some",
    "payload": {"model": "gpt-4", "messages": [{"role": "user", "content": "some"}]},
    "extra_headers": {"tenant_id": "hcscd"},
    "api_version": "str|null",
    "create_conversation": "bool"
}

The structure is restricted by the API, which is the only place where changes may
occur; it is not enforced by the database.

- `target_type` — type: enum, one of the values listed above. Used for execution-logic branching.
  `responses` behaves like `chat_completion` (same validation rules) but invokes the DIAL/OpenAI
  Responses API (a single `input`/`instructions` request) instead of the Chat Completions API.
- `method` — type: nullable enum, one of the values listed above. Not used for chat completion or responses.
- `url` — type: str. For chat completion and responses, a base URL passed to the SDK as-is; the SDK
  appends the endpoint path (`/chat/completions` or `/responses`). The exact base depends on the
  DIAL deployment — typically `.../openai/` for a DIAL Core proxy or
  `.../openai/deployments/{name}` when pointing at a specific deployment. The model/deployment is
  not encoded in the URL path — it is supplied via `payload.model`.
- `payload` — type: json. For chat completion and responses, the "stream" field is extracted if
  present, defaulting to false. When `stream` is true the executor uses the streaming SDK call and
  assembles the final result; otherwise (the default) it uses the non-streaming `.create()` call.
  Either way the stored result shape is identical. For both chat completion and responses,
  `payload.model` is required — it identifies the deployment, since the base URL carries no
  deployment path segment.
- `extra_headers` — type: dict[str, str]. The injected per-user auth header (resolved from the
  schedule's `service_id` via DIAL's on-behalf-of credentials, applied as `header_name:
  header_value`) always overrides any same-named entry in `extra_headers` — callers cannot use
  `extra_headers` to override or bypass the authenticated identity a schedule runs as.
- `api_version` — type: nullable str. Required for chat completion and responses.
- `create_conversation` — type: bool, defaults to `false`. Only allowed for `target_type`
  `chat_completion`; setting it `true` for `responses`/`rest`/`sse` is rejected with a validation error. When
  `true`, a successful run's prompt and assembled response are saved as a DIAL conversation under
  the run owner's storage bucket, titled after the schedule's `display_name`. Failure to create the
  conversation is non-fatal and does not affect the run outcome. Recommended value for
  `chat_completion` schedules is `true` — the field's OpenAPI description says so, so MCP clients
  creating a schedule opt in unless the caller asked for runs not to be saved.

Has a corresponding Pydantic model and is validated before each run start.

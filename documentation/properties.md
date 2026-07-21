API field `properties`, reflected by `schedules.kwargs` and equal to the actual
kwargs passed to `scheduler.add_job()`.

Represents the task run configuration and has the following structure:
{
    "target_type": "chat_completion|rest|sse",
    "method": "POST|PUT|GET|null",
    "url": "http://example.com/some",
    "payload": {"messages": [{"role": "user", "content": "some"}]},
    "extra_headers": {"tenant_id": "hcscd"},
    "api_version": "str|null",
    "create_conversation": "bool"
}

The structure is restricted by the API, which is the only place where changes may
occur; it is not enforced by the database.

- `target_type` — type: enum, one of the values listed above. Used for execution-logic branching.
- `method` — type: nullable enum, one of the values listed above. Not used for  chat completion.
- `url` — type: str. For chat completion, must be a valid OpenAI-compatible deployment URL.
- `payload` — type: json. For chat completion, the "stream" field is extracted if present, defaulting to false.
- `extra_headers` — type: dict[str, str]. The injected per-user auth header (resolved from the
  schedule's `service_id` via DIAL's on-behalf-of credentials, applied as `header_name:
  header_value`) always overrides any same-named entry in `extra_headers` — callers cannot use
  `extra_headers` to override or bypass the authenticated identity a schedule runs as.
- `api_version` — type: nullable str. Used only for chat completion.
- `create_conversation` — type: bool, defaults to `false`. Only allowed for `target_type`
  `chat_completion`; setting it `true` for `rest`/`sse` is rejected with a validation error. When
  `true`, a successful run's prompt and assembled response are saved as a DIAL conversation under
  the run owner's storage bucket. Failure to create the conversation is non-fatal and does not
  affect the run outcome.

Has a corresponding Pydantic model and is validated before each run start.

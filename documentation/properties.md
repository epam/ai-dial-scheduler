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
    "create_conversation": "bool",
    "timeout": "number|null",
    "idle_timeout": "number|null",
    "retry": {"interval_sequence": [2, 5], "retryable_errors": [500, 502]}
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
- `timeout` — type: nullable number, seconds, must be positive. The absolute ceiling on a single
  attempt, measured from the moment the request is built. It never resets while the attempt runs,
  so it is sized as a backstop against a run that never ends rather than as a routine bound. When
  omitted, the executing process supplies it: `*_HTTP_STREAMING_MAX_DURATION_SECONDS` (default
  3600) for a streaming target, `*_HTTP_MAX_DURATION_SECONDS` (default 600) otherwise. The longer
  ceiling is reserved for streaming because only a stream gives a progress signal that
  `idle_timeout` can watch; a non-streaming call is indistinguishable from a hung one at the
  transport layer, so total duration is its only instrument and must stay tighter.
  Enforced by the retry orchestrator via `asyncio.wait_for`.
- `idle_timeout` — type: nullable number, seconds, must be positive. The maximum gap between two
  consecutive stream events. It resets on every event, so it bounds inactivity rather than total
  duration, and must not exceed `timeout` when both are set. Applies to streaming targets only
  (`sse`, or `chat_completion`/`responses` with `payload.stream` true); it is accepted but inert
  for the rest. When omitted, the executing process's `*_HTTP_IDLE_TIMEOUT_SECONDS` applies
  (default 60).
  It is **not** an HTTP read timeout: it counts parsed events, because the DIAL SDK emits SSE
  heartbeat pings precisely when a response is idle and a transport-level bound would be reset by
  the very silence it is meant to detect.
- `retry` — type: nullable object. `interval_sequence` is a non-empty list of positive integer
  seconds whose length is the number of retries; `retryable_errors` is a non-empty list of HTTP
  status codes (100–599). A failed attempt is retried only when its status code is in that list.
  Timeouts of either kind carry no status code and are therefore always terminal.

Neither timeout is retryable, and neither requires a database migration — both persist inside the
existing `schedules.kwargs` JSONB. A ceiling expiry logs as `attempt_timed_out` and an idle expiry
as `attempt_idle_timed_out`, so the two are distinguishable without reading the error text.

Has a corresponding Pydantic model and is validated before each run start.

API field `properties`, reflected by `schedules.kwargs` and equal to the actual
kwargs passed to `scheduler.add_job()`.

Represents the task run configuration and has the following structure:
{
    "schedule_id": "str",
    "target_type": "chat_completion|rest|sse",
    "method": "POST|PUT|GET|null",
    "url": "http://example.com/some",
    "payload": {"messages": [{"role": "user", "content": "some"}]},
    "extra_headers": [{"tenant_id": "hcscd"}],
    "api_version": "str|null"
}

The structure is restricted by the API, which is the only place where changes may
occur; it is not enforced by the database.

- `schedule_id` — type: str. Equals `apscheduler_jobs.id`; used to reference the  schedule during execution.
- `target_type` — type: enum, one of the values listed above. Used for execution-logic branching.
- `method` — type: nullable enum, one of the values listed above. Not used for  chat completion.
- `url` — type: str. For chat completion, must be a valid OpenAI-compatible deployment URL.
- `payload` — type: json. For chat completion, the "stream" field is extracted if present, defaulting to false.
- `extra_headers` — type: dict[str, str]. If any injected headers are present, `extra_headers` overrides them.
- `api_version` — type: nullable str. Used only for chat completion.

Has a corresponding Pydantic model and is validated before each run start.
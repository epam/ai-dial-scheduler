# Quickstart: Validating the Conversation ID Header and User-Agent

Prerequisites: `uv sync`. Branch `feature/scheduler-user-agent`. For the dev checks: access to Grafana (Loki, Tempo, ADAS
ClickHouse) and DIAL Admin on the development environment.

## 1. Automated checks

```
uv run pytest tests/unit/lib/utils/test_conversation_header.py \
              tests/unit/lib/utils/test_user_agent.py tests/unit/lib/clients/test_http_client.py \
              tests/unit/worker/test_conversation.py tests/unit/worker/test_jobs.py \
              tests/unit/worker/executors/
make lint
make test
```

Expected: all pass. The new tests cover (see [contracts/outbound-headers.md](contracts/outbound-headers.md) and
[contracts/log-events.md](contracts/log-events.md)):
- encoding: ASCII unchanged, Cyrillic, emoji, control characters, literal `%25`;
- header present only for `chat_completion` + `create_conversation`, identical on every retry attempt, equal to the PUT path;
- caller `x-conversation-id` (any casing) replaced; passed through when the scheduler sends none;
- bucket failure / missing schedule / invalid `payload.model` → no header, one warning with `trace_id`/`span_id`, run outcome
  unchanged, no PUT, `conversation_create_skipped` logged;
- `responses`, `rest`, `sse` never add the header; all executors send `User-Agent: ai-dial-scheduler/<version>`.

## 2. Manual end-to-end (any DIAL environment)

1. Create a chat completion schedule with `create_conversation: true` and run it now (`POST /v1/schedules/{id}/run`).
2. In DIAL Core's prompt log or trace, the run's request carries the conversation id and the scheduler user agent.
3. `GET /v1/schedules/{id}/runs/{run_id}` → `conversation_id` equals the value DIAL Core recorded.
4. Repeat with `display_name: "Щоденний звіт"`: the run succeeds; DIAL Core records the percent-encoded id
   (`encode(conversation_id)`, see [contracts/outbound-headers.md](contracts/outbound-headers.md)).
5. Repeat with a display name whose encoded id exceeds 4096 bytes: the run succeeds, the request carries no
   `X-CONVERSATION-ID`, `conversation_id_resolve_failed` is logged and no conversation is saved.

## 3. Failure path

Run a schedule whose owner's bucket lookup fails. Expected: the run still executes and its outcome matches the target's
response; the worker log shows exactly one `conversation_id_resolve_failed` warning and, on success,
`conversation_create_skipped`; the run's `conversation_id` is `null`.

## 4. Development environment verification (after deploy)

Use a dev schedule with `create_conversation: true` and a recent run id `RUN_ID` / conversation id `CONV_ID` from the API.

1. **Tempo** — search `{resource.service.name="dial-scheduler-worker" && name =~ "job:schedule.*"}`, open a trace:
   - traefik span: `user_agent.original = "ai-dial-scheduler/<version>"` (`0.0.0` on dev builds);
   - dial-core `POST /openai/deployments/{id}/chat/completions` span: `gen_ai.conversation.id = CONV_ID`.
2. **ADAS ClickHouse** (`adas-clickhouse`):
   ```sql
   SELECT l.request_time, l.chat_id, l.deployment, c.client_user_agent, c.client_session_source, c.client_type
   FROM dial_usage_log l LEFT JOIN usage_client_identity c ON c.event_id = l.event_id
   WHERE l.request_time > now() - INTERVAL 1 DAY AND l.chat_id LIKE '%RUN_ID%'
   ORDER BY l.request_time
   ```
   Expected: first row is the scheduler call with `chat_id = CONV_ID`, `client_user_agent` = scheduler, `client_session_source
   = 'chat_id'` (`client_type` stays `chat` until ADAS adds a scheduler rule).
3. **Loki** — `{namespace="dial-scheduler", app="worker"} |= "RUN_ID" | json`: entries carry `trace_id`, `span_id` and
   `conversation_id`; `{namespace="dial-scheduler", app="worker"} |= "conversation_id_resolve_failed"` is empty under normal
   operation.
4. **DIAL Admin** — filter sessions by `CONV_ID`: the scheduler call is listed. Open the conversation in DIAL Chat, send one
   message, refresh: the follow-up is in the same session.
5. **Non-ASCII check** — repeat steps 2 and 4 for a schedule named in Cyrillic, using the encoded form of `CONV_ID`; the
   `chat_id` DIAL Chat sends on continuation equals the scheduler's encoded value.

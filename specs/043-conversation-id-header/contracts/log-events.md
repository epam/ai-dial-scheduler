# Contract: Run Log Events

Operator-facing structured log contract for this feature (constitution II). All events are emitted via
`src.lib.utils.logging.logger`, inside the run span when tracing is enabled, so each also carries `timestamp`, `level`,
`logger`, `trace_id`, `span_id` and the run's bound context (`schedule_id`, `run_id`, and — once resolved — `conversation_id`).

No event contains credentials, OBO header values, prompts or responses.

## New events

| Event                             | Level   | When                                                                                   | Fields                                                                 |
|-----------------------------------|---------|----------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| `conversation_id_resolved`        | info    | Location resolved before the target request                                            | `run_id`, `schedule_id`, `deployment_kind` (`application`\|`model`)    |
| `conversation_id_resolve_failed`  | warning | Location could not be resolved (incl. encoded id over 4096 bytes); request will be sent without `X-CONVERSATION-ID` | `run_id`, `schedule_id`, `reason` (exception text, no secrets)         |
| `conversation_create_skipped`     | info    | Run succeeded, `create_conversation: true`, but no location → no conversation saved    | `run_id`, `schedule_id`, `reason = "conversation_id_unresolved"`       |

Exactly one `conversation_id_resolve_failed` per affected run (resolution is attempted once, not per attempt).

## Changed events

| Event                        | Change                                                                                         |
|------------------------------|------------------------------------------------------------------------------------------------|
| `conversation_create_start`  | Emitted post-run as today; no longer preceded by a bucket lookup (location reused).            |
| all run events after resolve | Carry `conversation_id` via log context (e.g. `attempt_*`, `run_complete`, `run_failed`).      |

Unchanged: `conversation_create_succeeded`, `conversation_create_failed`, `conversation_id_persisted`,
`conversation_id_persist_failed`.

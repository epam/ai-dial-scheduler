# Quickstart: Persist Conversation ID for Schedule Runs

**Spec**: [spec.md](./spec.md)

This guide verifies the feature end-to-end against a running API + worker. Replace `$API`,
`$SCHEDULE_ID`, `$RUN_ID`, and the DIAL auth header value as appropriate.

```bash
API=http://localhost:8000
AUTH='Authorization: Bearer <dial-user-token>'   # or the project's api-key header
```

Apply the migration first:

```bash
uv run alembic upgrade head
```

---

## User Story 1 — See the conversation for a run in the runs list (P1)

Covers acceptance scenarios **1.1** (id retained on the run), **1.2** (id in the list), **1.3**
(id stable on re-listing). Success criteria SC-001, SC-003, SC-005.

1. Create a chat-completion schedule with conversation creation enabled (frequent trigger so a run
   fires quickly):

```bash
curl -sS -X POST "$API/v1/schedules/" -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "display_name": "QS conversation run",
  "trigger": {"type": "interval", "seconds": 30},
  "properties": {
    "target_type": "chat_completion",
    "create_conversation": true,
    "payload": {"model": "gpt-4", "messages": [{"role": "user", "content": "hello"}]}
  }
}'
# → note "id" as $SCHEDULE_ID
```

2. Wait for one run to fire and complete successfully (worker running). Then list the runs:

```bash
curl -sS "$API/v1/schedules/$SCHEDULE_ID/runs" -H "$AUTH" | jq '.results[]'
```

**Expect (scenario 1.2, SC-001)**: the successful run entry has a non-null
`conversation_id` beginning with `conversations/` and ending in
`...__QS conversation run__<run-id>`.

3. Fetch the same run's detail and confirm the id matches (scenario 1.1, consistency):

```bash
RUN_ID=<id from step 2>
curl -sS "$API/v1/schedules/$SCHEDULE_ID/runs/$RUN_ID" -H "$AUTH" | jq '.conversation_id'
```

**Expect**: identical to the list value.

4. Open that id in DIAL storage / DIAL Chat to confirm it addresses the created conversation
   (SC-003) — no name search or log inspection required.

5. Re-list the runs and confirm the id is unchanged (scenario 1.3, FR-007, SC-005):

```bash
curl -sS "$API/v1/schedules/$SCHEDULE_ID/runs" -H "$AUTH" | jq '.results[] | select(.id=="'$RUN_ID'") | .conversation_id'
```

**Expect**: the exact same string as step 2.

---

## User Story 2 — Distinguish runs that produced no conversation (P1)

Covers acceptance scenarios **2.1** (creation disabled → null), **2.2** (unsuccessful run → null),
**2.3** (success but conversation creation did not complete → null). Success criteria SC-002, SC-004.

### 2.1 — conversation creation disabled

```bash
curl -sS -X POST "$API/v1/schedules/" -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "display_name": "QS no conversation",
  "trigger": {"type": "interval", "seconds": 30},
  "properties": {
    "target_type": "chat_completion",
    "create_conversation": false,
    "payload": {"model": "gpt-4", "messages": [{"role": "user", "content": "hello"}]}
  }
}'
# → $SCHEDULE_ID_DISABLED ; wait for a successful run, then:
curl -sS "$API/v1/schedules/$SCHEDULE_ID_DISABLED/runs" -H "$AUTH" | jq '.results[].conversation_id'
```

**Expect (scenario 2.1, SC-002)**: `null` for the successful run.

### 2.2 — run that did not complete successfully

Use a schedule whose target request fails (e.g. an unreachable model / bad payload), let one run
record as `error`, then:

```bash
curl -sS "$API/v1/schedules/$SCHEDULE_ID_FAILING/runs" -H "$AUTH" \
  | jq '.results[] | {status, conversation_id}'
```

**Expect (scenario 2.2)**: `status: "error"` (or `in_progress` while running) with
`conversation_id: null`. The run outcome is unchanged by this feature (SC-004).

### 2.3 — success but conversation creation did not complete

Simulate a DIAL storage failure during conversation creation (e.g. DIAL storage unavailable) on a
`create_conversation: true` chat-completion schedule, let one run record `success`, then:

```bash
curl -sS "$API/v1/schedules/$SCHEDULE_ID_CONV_FAIL/runs" -H "$AUTH" \
  | jq '.results[] | {status, conversation_id}'
```

**Expect (scenario 2.3, SC-002, SC-004)**: `status: "success"` with `conversation_id: null` — the
failed conversation creation did not change the run's success outcome, and no partial id was
persisted (FR-003). Worker logs show `conversation_create_failed` (or
`conversation_id_persist_failed`), not `conversation_id_persisted`.

---

## Edge checks

- **Historical run** (recorded before the migration): its list entry shows `conversation_id: null`
  (FR-005). Verify against any run that predates the deploy.
- **In-progress run**: while a run is `in_progress`, its `conversation_id` is `null`.
- **Non-chat-completion schedule** (e.g. `responses` type): every run reports `conversation_id:
  null`.

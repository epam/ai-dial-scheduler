# DIAL Scheduler — Configuration Reference

This file contains the full configuration reference for a **schedule** in DIAL Scheduler: the
configuration model, trigger definition, run properties (per target type), retry policy, metadata,
the owner-identity (`service_id`) contract, and examples for creating and running schedules. A
schedule is the unit of configuration — you create one by `POST`-ing the JSON manifest documented
here, and replace it wholesale with `PUT`.

For the HTTP API surface (endpoints, responses, error codes) see
[documentation/api_contract.md](./documentation/api_contract.md). For the deployment/manifest that
registers the scheduler as a DIAL application see
[documentation/deployment/deployment.md](./documentation/deployment/deployment.md). For
environment variables (database, HTTP, worker health, DIAL auth) see
[README.md](./README.md#environment-variables).

## Configuration model

A schedule is defined by a JSON body validated by a Pydantic v2 model
(`src/lib/schemas/schedule.py`) before it is persisted and before **every** run. The body is
accepted by two endpoints with an identical shape:

- `POST /schedules/` — create a schedule and register its job.
- `PUT /schedules/{schedule_id}` — replace **all** fields of an existing schedule and re-register
  its job. `PUT` is a full replacement, not a partial patch.

The stored `properties` object equals the kwargs passed to APScheduler's `add_job()` and is
re-validated before each run — see [documentation/properties.md](./documentation/properties.md).

## Schedule configuration

<details>
<summary><b>Configuration JSON Sample</b></summary>

<br>Here is a full example of a `chat_completion` schedule that fires every morning at 09:00 UTC,
saves each run as a DIAL conversation, retries twice on `502`/`503`, and carries folder/tenant
metadata:

```json
{
  "display_name": "Daily GPT-4o summary",
  "service_id": "dial-native",
  "description": "Posts a GPT-4o summary of the day's highlights every morning.",
  "trigger": {
    "cron": {
      "fields": {
        "hour": 9,
        "minute": 0
      },
      "start_date": "2026-01-01T00:00:00Z",
      "end_date": null
    }
  },
  "properties": {
    "target_type": "chat_completion",
    "url": "http://dial-core.dial.svc.cluster.local/openai/",
    "payload": {
      "model": "gpt-4o",
      "messages": [
        {
          "role": "user",
          "content": "Summarise today's highlights."
        }
      ],
      "stream": true
    },
    "api_version": "2025-01-01-preview",
    "extra_headers": {
      "tenant_id": "hcscd"
    },
    "timeout": 120.0,
    "retry": {
      "interval_sequence": [5, 30],
      "retryable_errors": [502, 503]
    },
    "create_conversation": true
  },
  "metadata": {
    "folder": "reports/daily",
    "tenant": "acme"
  }
}
```

A one-time `rest` schedule that fires once at a specific instant:

```json
{
  "display_name": "One-off REST ping",
  "service_id": "dial-native",
  "trigger": {
    "date": "2026-09-01T12:30:00Z"
  },
  "properties": {
    "target_type": "rest",
    "method": "POST",
    "url": "https://example.com/api/ping",
    "payload": {
      "source": "scheduler"
    }
  }
}
```

</details>

## Main Configuration Structure

| Field          | Required | Type         | Description                                                                                                                                                        | Available Values | Default Value |
|----------------|----------|--------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------|---------------|
| `display_name` | Yes      | String       | Human-readable schedule name. Must not be empty or whitespace-only. Used as the DIAL conversation title when `create_conversation` is `true`.                      | -                | -             |
| `service_id`   | Yes      | String       | DIAL external service whose per-user on-behalf-of (OBO) credentials authenticate every run of this schedule. See [service_id configuration](#service_id-owner-identity). | e.g. `dial-native` | -             |
| `description`  | No       | String or null | Free-text notes about the schedule. Max length **1000** chars. `null` when absent.                                                                              | -                | `null`        |
| `trigger`      | Yes      | Object       | When the schedule fires. Exactly one of `cron`/`date`. See [Trigger configuration](#trigger-configuration).                                                        | -                | -             |
| `properties`   | Yes      | Object       | What the schedule does when it fires (the target call). Rules depend on `target_type`. See [Properties configuration](#properties-configuration).                  | -                | -             |
| `metadata`     | No       | Object       | Opaque string-keyed tags for filtering/organisation. See [Metadata configuration](#metadata-configuration).                                                        | -                | `{}`          |

Read-only response fields (`id`, `next_run_time`, `is_deleted`, `created_by`, `created_at`,
`updated_at`) are never accepted in a request body. `is_deleted` in particular is **ignored** (not
rejected) if sent — see [documentation/api_contract.md](./documentation/api_contract.md).

### Trigger configuration

Exactly one of `cron` or `date` must be provided; supplying both, or neither, is rejected with a
validation error (HTTP 422). All triggers are anchored to **UTC**
(`src/lib/services/trigger_factory.py`).

| Field  | Required                    | Type           | Description                                                       | Available Values | Default Value |
|--------|-----------------------------|----------------|-----------------------------------------------------------------|------------------|---------------|
| `cron` | Yes (if `date` is absent)   | Object or null | Recurring cron specification. See [Cron trigger](#cron-trigger). | -                | `null`        |
| `date` | Yes (if `cron` is absent)   | Datetime or null | One-time fire at a single instant (ISO-8601). Interpreted as UTC. | -                | `null`        |

The derived `trigger_type` (`cron` or `date`) is returned in list responses; it is inferred, not
supplied.

#### Cron trigger

| Field        | Required | Type           | Description                                                        | Default Value |
|--------------|----------|----------------|-------------------------------------------------------------------|---------------|
| `fields`     | Yes      | Object         | APScheduler `CronTrigger` field set. See [Cron fields](#cron-fields). | -             |
| `start_date` | No       | Datetime or null | Do not fire before this instant (ISO-8601, UTC).                 | `null`        |
| `end_date`   | No       | Datetime or null | Do not fire after this instant (ISO-8601, UTC).                  | `null`        |

##### Cron fields

Each field maps directly to an APScheduler `CronTrigger` field. All fields are optional; omitted
fields default to APScheduler's cron semantics. Every field accepts a string cron expression
(e.g. `"*/5"`, `"mon-fri"`), an integer, or `null`.

| Field         | Required | Type                    | Description                        | Example    |
|---------------|----------|-------------------------|------------------------------------|------------|
| `year`        | No       | String, Integer or null | 4-digit year                       | `2026`     |
| `month`       | No       | String, Integer or null | Month (1–12)                       | `"*/2"`    |
| `day`         | No       | String, Integer or null | Day of month (1–31)                | `1`        |
| `week`        | No       | String, Integer or null | ISO week (1–53)                    | `"*"`      |
| `day_of_week` | No       | String, Integer or null | Day of week (`0`–`6` or `mon`–`sun`) | `"mon-fri"` |
| `hour`        | No       | String, Integer or null | Hour (0–23)                        | `9`        |
| `minute`      | No       | String, Integer or null | Minute (0–59)                      | `"*/5"`    |
| `second`      | No       | String, Integer or null | Second (0–59)                      | `0`        |

<details>
<summary><b>Trigger configuration JSON sample</b></summary>

Every weekday at 08:30 UTC:

```json
{
  "trigger": {
    "cron": {
      "fields": {
        "day_of_week": "mon-fri",
        "hour": 8,
        "minute": 30
      }
    }
  }
}
```

Every 5 minutes, only during January 2026:

```json
{
  "trigger": {
    "cron": {
      "fields": {
        "minute": "*/5"
      },
      "start_date": "2026-01-01T00:00:00Z",
      "end_date": "2026-02-01T00:00:00Z"
    }
  }
}
```

One-time fire:

```json
{
  "trigger": {
    "date": "2026-09-01T12:30:00Z"
  }
}
```

</details>

### Properties configuration

`properties` describes the target call made when the schedule fires. Validation rules **branch on
`target_type`**: the two model-style types (`chat_completion`, `responses`) and the two HTTP-style
types (`rest`, `sse`) accept different fields. Rules are enforced by the API and re-validated
before each run.

| Field                 | Required                                    | Type              | Description                                                                                                                                                            | Available Values                                  | Default Value |
|-----------------------|---------------------------------------------|-------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------|---------------|
| `target_type`         | Yes                                         | String            | Selects the execution style and which rules below apply.                                                                                                              | `chat_completion`, `responses`, `rest`, `sse`     | -             |
| `url`                 | Yes                                         | String            | Target URL. Must be a valid HTTP/HTTPS URL. For model types this is a base URL; the SDK appends `/chat/completions` or `/responses`.                                   | -                                                 | -             |
| `method`              | Conditional                                 | String or null    | HTTP method. **Required** for `rest`/`sse`; **must not be set** for `chat_completion`/`responses`.                                                                     | `POST`, `PUT`, `GET`                              | `null`        |
| `payload`             | No                                          | Object            | Request body. For model types, `payload.model` is **required** and `stream` is honoured (defaults to `false` if absent). For HTTP types, sent as the request payload. | -                                                 | `{}`          |
| `extra_headers`       | No                                          | Dict[String,String] | Extra request headers. The injected OBO auth header always overrides a same-named entry — callers cannot bypass the authenticated identity.                          | -                                                 | `{}`          |
| `api_version`         | Conditional                                 | String or null    | **Required** for `chat_completion`/`responses`; **must not be set** for `rest`/`sse`.                                                                                  | -                                                 | `null`        |
| `timeout`             | No                                          | Float or null     | Per-run request timeout in seconds. Must be a positive number when set.                                                                                               | -                                                 | `null`        |
| `retry`               | No                                          | Object or null    | Per-schedule retry policy for a failed dispatch. See [Retry policy configuration](#retry-policy-configuration).                                                        | -                                                 | `null`        |
| `create_conversation` | No                                          | Boolean           | Save each successful run as a DIAL conversation in the owner's storage, titled after `display_name`. **Only allowed for `chat_completion`**; rejected otherwise. Conversation creation is best-effort — a failure is logged and never fails the run. | `true`, `false`  | `false`       |

#### Target-type rules

| `target_type`     | `method`          | `api_version`     | `payload.model` | `stream`                          | `create_conversation` |
|-------------------|-------------------|-------------------|-----------------|-----------------------------------|-----------------------|
| `chat_completion` | must **not** be set | **required**    | **required**    | honoured; defaults to `false`     | allowed               |
| `responses`       | must **not** be set | **required**    | **required**    | honoured; defaults to `false`     | rejected              |
| `rest`            | **required**      | must **not** be set | not applicable | not applicable                    | rejected              |
| `sse`             | **required**      | must **not** be set | not applicable | not applicable                    | rejected              |

- `chat_completion` calls a DIAL Chat Completions endpoint. `responses` behaves identically for
  validation but invokes the DIAL/OpenAI Responses API (`/responses`) instead.
- `url` for model types is the base (e.g. `.../openai/` for a DIAL Core proxy). The deployment is
  **not** encoded in the URL path — it is supplied via `payload.model`.
- For `chat_completion`/`responses`, if `stream` is absent from `payload` it is set to `false`; the
  stored result shape is identical whether streamed or not.

<details>
<summary><b>Properties configuration JSON samples</b></summary>

Chat completion (streamed, saved as a conversation):

```json
{
  "properties": {
    "target_type": "chat_completion",
    "url": "http://dial-core.dial.svc.cluster.local/openai/",
    "payload": {
      "model": "gpt-4o",
      "messages": [{ "role": "user", "content": "Summarise today's highlights." }],
      "stream": true
    },
    "api_version": "2025-01-01-preview",
    "create_conversation": true
  }
}
```

REST POST call:

```json
{
  "properties": {
    "target_type": "rest",
    "method": "POST",
    "url": "https://example.com/api/ping",
    "payload": { "source": "scheduler" }
  }
}
```

SSE streaming call every 5 minutes:

```json
{
  "properties": {
    "target_type": "sse",
    "method": "POST",
    "url": "https://example.com/api/stream",
    "payload": { "prompt": "Generate a status update." }
  }
}
```

</details>

#### Retry policy configuration

Optional per-schedule policy that retries a **failed dispatch** of the target action. It does not
apply to credential-resolution failures (those are terminal — see
[service_id configuration](#service_id-owner-identity)).

| Field                | Required | Type          | Description                                                                                                                          | Constraints                                          | Default Value |
|----------------------|----------|---------------|------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------|---------------|
| `interval_sequence`  | Yes      | Array[Float]  | Ordered seconds to wait before each successive retry. Its length defines the retry count (**total attempts = 1 + length**).          | Non-empty; every entry must be a positive number      | -             |
| `retryable_errors`   | Yes      | Array[Integer] | HTTP status codes that make a failed attempt eligible for retry.                                                                    | Non-empty; each a valid HTTP status code (`100`–`599`) | -             |

<details>
<summary><b>Retry policy configuration JSON sample</b></summary>

Retry up to twice — 5s then 30s — only on `502` and `503` (three attempts total):

```json
{
  "retry": {
    "interval_sequence": [5, 30],
    "retryable_errors": [502, 503]
  }
}
```

</details>

### service_id (owner identity)

`service_id` is **required** on create and update. It names a DIAL external service (registered in
the scheduler's app definition under `external_services`, e.g. `dial-native`) whose per-user
on-behalf-of (OBO) credentials authenticate **every** run of this schedule.

- Before each fire, the worker resolves OBO credentials for the schedule's **owner** (`created_by`,
  taken from the caller's identity at create time) and `service_id`, and applies them as the
  outbound auth header.
- If the owner has not enabled offline access, or an administrator has not approved this
  application for the service, the run is recorded as failed with a **credentials-stage** error
  (`result.stage == "credentials"`), the target action is **never dispatched**, and the failure
  does **not** consume the retry budget.
- The injected auth header always overrides a same-named entry in `properties.extra_headers` —
  callers cannot use `extra_headers` to bypass the authenticated identity.
- Schedules created before this field existed were backfilled with `service_id = "dial-oauth"`,
  then moved to `"dial-native"` when the scheduler adopted DIAL-native offline delegation.

See [documentation/deployment/deployment.md](./documentation/deployment/deployment.md) for
registering external services and granting consent.

### Metadata configuration

`metadata` is an opaque, string-keyed map for organising and filtering schedules (e.g. folder
hierarchy, tenant, owner, environment). The scheduler **stores and filters** on it but never
interprets its meaning.

| Field    | Required | Type   | Description                                                                                          | Default Value |
|----------|----------|--------|----------------------------------------------------------------------------------------------------|---------------|
| `{key}`  | No       | Any    | Arbitrary tag. Only **string-valued** entries are filterable via the list endpoint's `metadata__<key>=<value>` query parameters. | -             |

Filtering matches schedules whose metadata contains **all** supplied key/value pairs as strings
(JSONB containment, composed with AND). See the List endpoint in
[documentation/api_contract.md](./documentation/api_contract.md).

<details>
<summary><b>Metadata configuration JSON sample</b></summary>

```json
{
  "metadata": {
    "folder": "reports/daily",
    "tenant": "acme",
    "env": "prod"
  }
}
```

Filter for it: `GET /schedules/?metadata__tenant=acme&metadata__env=prod`.

</details>

## List query parameters

The list endpoint (`GET /schedules/`) accepts, in addition to `metadata__<key>` filters:

| Parameter    | Type    | Description                                                                                     | Available Values                          | Default Value |
|--------------|---------|------------------------------------------------------------------------------------------------|-------------------------------------------|---------------|
| `limit`      | Integer | Page size (≥ 1).                                                                                | -                                         | `20`          |
| `offset`     | Integer | Rows to skip (≥ 0).                                                                             | -                                         | `0`           |
| `name`       | String  | Case-insensitive substring match on `display_name` (LIKE wildcards escaped). Composes with AND. | -                                         | -             |
| `order_by`   | String  | Sort field. Inactive/paused schedules always sort last when ordering by `next_run_time`.        | `created_at`, `next_run_time`, `name`     | `created_at`  |
| `order_dir`  | String  | Sort direction.                                                                                 | `asc`, `desc`                             | `desc`        |
| `is_deleted` | Boolean | Deletion-state filter. `false` returns live schedules, `true` returns soft-deleted ones; there is no "both". Echoed into page links. | `true`, `false` | `false`       |

## Behavior notes

- **Full replacement on update.** `PUT /schedules/{id}` replaces every field; there is no partial
  patch. A soft-deleted schedule cannot be updated (returns 409).
- **Validated twice.** `properties` is validated at the API boundary and again before each run, so
  a stored schedule that violates the target-type rules can never dispatch.
- **UTC everywhere.** All trigger datetimes (`date`, `start_date`, `end_date`) are anchored to UTC.
- **At-least-once firing.** Consumers of dispatched actions should be idempotent where it matters
  (see [documentation/requirements.md](./documentation/requirements.md)).
- **Deletion preserves history.** Deleting a schedule with any run history keeps the row with
  `is_deleted = true` (run records survive); deleting one with no runs removes it outright. Neither
  is restorable — see [documentation/api_contract.md](./documentation/api_contract.md).

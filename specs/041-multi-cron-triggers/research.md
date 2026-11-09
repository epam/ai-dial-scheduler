# Research: Multiple Cron Triggers per Schedule

## R1 — How to fire one job from several cron specs

- **Decision**: Build one `CronTrigger` per entry and wrap them in APScheduler's `OrTrigger` (`apscheduler.triggers.combining`). With a single entry, return the plain `CronTrigger`, as today.
- **Rationale**: `OrTrigger.get_next_fire_time` returns the minimum next fire time across children. This directly gives FR-003 (any entry fires), FR-004 (coinciding entries yield one fire time, so one run) and FR-005 (earliest upcoming). Children are `CronTrigger`s, but the "previous fire time from another child" caveat in the APScheduler docs does apply to `start_date`: a plain `CronTrigger` reads `start_date` only when the previous fire time is `None`, so after a sibling fires it would ignore its own start. Children are therefore a small `CronTrigger` subclass (`EntryCronTrigger`) that discards a previous fire time earlier than its own `start_date`. `end_date` is unaffected. `OrTrigger` pickles children via `__getstate__`, so it works with the existing SQLAlchemy job store. The job id stays the schedule id, so pause, resume, delete and `next_run_time` need no change.
- **Alternatives considered**:
  - *One APScheduler job per entry* (ids like `<schedule>#0`). Rejected: pause, resume, delete and next-run lookups would have to fan out, and a partial failure would leave a half-registered schedule.
  - *Compute the union in application code*. Rejected: reimplements what `OrTrigger` does.
  - *`AndTrigger`*. Wrong semantics (intersection).

## R2 — Shape of the list in the request

- **Decision**: Add a new optional field `crons: list[CronTriggerSpec]` to `ScheduleTrigger`, next to `cron` and `date`. Exactly one of `cron`, `crons`, `date` must be set.
- **Rationale**: Additive, so existing clients, stored rows and the OpenAPI contract for `cron` are untouched (FR-009, FR-010, constitution IV: not a breaking change, so no major contract bump). It also keeps the schema obvious to MCP agents, which read field names and descriptions as tool input.
- **Alternatives considered**:
  - *Make `cron` accept an object or a list*. Rejected: a union type breaks generated clients and weakens the MCP tool schema, and it is a breaking contract change.
  - *Always convert single cron to a one-item list in storage*. Rejected: violates FR-010 (no rewriting) and FR-014 (return what was submitted).

## R3 — Storage

- **Decision**: No schema or migration change. `schedules.trigger` is JSONB and already stores `ScheduleTrigger.model_dump(mode="json")`.
- **Rationale**: New rows gain a `crons` key. Rows written before the change have no `crons` key and still validate, because the field defaults to `None`. Readers must treat a missing key as `null`; this is noted in the contract.
- **Alternatives considered**: a new column or table for entries. Rejected as unnecessary (YAGNI, constitution V).

## R4 — Maximum number of entries

- **Decision**: 20 entries, held as a constant in `src/lib/constants.py` and enforced by the schema.
- **Rationale**: Realistic timetables (per-weekday times) need at most a handful. A fixed bound keeps `next_run_time` computation and the stored JSON small. 20 covers a dozen distinct patterns with headroom.
- **Alternatives considered**: a settings-driven limit (rejected: no operational need, adds configuration surface); no limit (rejected by FR-012).

## R5 — Per-entry validation errors

- **Decision**: Rely on Pydantic's list validation. Errors already carry the location `trigger.crons.<index>...`, which satisfies FR-008 without custom code. An empty list is rejected by `min_length=1`.
- **Alternatives considered**: custom validator producing prose messages. Rejected as redundant.

## R6 — Date bounds

- **Decision**: Each entry keeps its own `start_date` and `end_date`, passed to its own child trigger (an `EntryCronTrigger`, see R1). `OrTrigger` finishes only when every child has finished.
- **Rationale**: Falls out of reusing `CronTriggerSpec`; an expired schedule reports no `next_run_time`, same as today (spec edge cases).
- **Caveat**: honouring `start_date` per entry needs the `EntryCronTrigger` subclass described in R1; covered by `TestBuildTriggerCronsStartDate`.

## R7 — Trigger type and response shape

- **Decision**: `ScheduleTrigger.trigger_type` returns `CRON` when `cron` or `crons` is set. Responses return the stored dict unchanged. Newly written rows therefore include `"crons": null` (or `"cron": null` when a list is used), matching how `"date": null` is already written.
- **Rationale**: Keeps FR-014 and avoids introducing a second serialization mode.

## R8 — Duplicate coinciding fires

- **Decision**: No extra de-duplication. `OrTrigger` returns one datetime per evaluation, and APScheduler runs the job once per returned fire time. Duplicate entries therefore produce one run.
- **Verification**: covered by a unit test on `OrTrigger.get_next_fire_time` with identical entries, and a run-count assertion in the integration test.

## R9 — Field-value validation

- **Decision**: `CronTriggerSpec` builds a throwaway `CronTrigger` in a model validator, so out-of-range values (for example `hour: 25`) are rejected with a 422 that locates the entry. This also applies to a single `cron` (previously a 502 at registration), noted in the CHANGELOG under Changed.
- **Alternatives considered**: catching `ValueError` in the service and mapping it to 422. Rejected: the row is already persisted by then, and the error would not carry the entry index.

# Schema Contract: `schedules` & `schedule_runs`

**Contract type**: Database schema (this feature exposes no external API; the DB tables are
the interface that downstream features — repositories, services, the API layer — depend on).

**Version**: 1.0.0 (initial). Breaking changes to column names/types/constraints below MUST
bump the major version.

## Table `schedules`

```
schedules
  id                VARCHAR(191)  NOT NULL  PRIMARY KEY        -- = apscheduler_jobs.id (logical, no FK)
  created_at        TIMESTAMPTZ   NOT NULL  DEFAULT now()
  updated_at        TIMESTAMPTZ   NOT NULL  DEFAULT now()      -- ON UPDATE now()
  display_name      TEXT          NOT NULL
  created_by        TEXT          NOT NULL
  trigger           JSONB         NOT NULL
  kwargs            JSONB         NOT NULL
  CONSTRAINT PK_schedules PRIMARY KEY (id)
```

**Consumer guarantees**:
- `id` is type-compatible with `apscheduler_jobs.id` (`VARCHAR(191)`) → join with no cast.
- No foreign key to `apscheduler_jobs` exists; consumers MUST tolerate a missing engine row.

## Table `schedule_runs`

```
schedule_runs
  id                 UUID         NOT NULL  PRIMARY KEY        -- app-generated uuid4
  created_at         TIMESTAMPTZ  NOT NULL  DEFAULT now()
  updated_at         TIMESTAMPTZ  NOT NULL  DEFAULT now()      -- ON UPDATE now()
  end_time           TIMESTAMPTZ  NULL                         -- NULL while in_progress
  status             TEXT         NOT NULL                     -- CHECK constrained
  result             JSONB        NULL                         -- NULL until finished
  schedule_id        VARCHAR(191) NOT NULL                     -- FK -> schedules.id
  schedule_snapshot  JSONB        NOT NULL                     -- {display_name, trigger, kwargs}
  CONSTRAINT PK_schedule_runs PRIMARY KEY (id)
  CONSTRAINT CK_schedule_runs_status CHECK (status IN ('success','error','in_progress','missed'))
  CONSTRAINT FK_schedule_runs_schedule_id_schedules
      FOREIGN KEY (schedule_id) REFERENCES schedules (id) ON DELETE RESTRICT
  INDEX IX_schedule_runs_schedule_id (schedule_id)
```

**Consumer guarantees**:
- `status` is always one of the four literal values (DB-enforced).
- Deleting a `schedules` row referenced by any run is rejected (`RESTRICT`).
- `end_time` and `result` MAY be `NULL` (in-progress runs); consumers MUST handle `NULL`.
- `schedule_snapshot` is self-contained and immutable per run; it does NOT track later edits
  to the parent schedule.

## Status enumeration (application-side)

`ScheduleRunStatus(StrEnum)` in `src/lib/db/enums.py`:

```
SUCCESS      = "success"
ERROR        = "error"
IN_PROGRESS  = "in_progress"
MISSED       = "missed"
```

The DB CHECK constraint values MUST stay in sync with this enum.

## External (not owned) — `apscheduler_jobs`

Read-only join source, owned by APScheduler. Columns relied upon:

```
apscheduler_jobs
  id             VARCHAR(191)      PRIMARY KEY
  next_run_time  DOUBLE PRECISION  NULL      -- epoch seconds; NULL when paused
```

This feature never creates, alters, or drops this table.

## Join contract

```
SELECT s.*, j.next_run_time
FROM schedules AS s
LEFT OUTER JOIN apscheduler_jobs AS j ON s.id = j.id
```

Returns one row per `schedules` row (LEFT JOIN); `next_run_time` is `NULL` when the engine
row is absent or the job is paused.

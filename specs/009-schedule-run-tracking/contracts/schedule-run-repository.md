# Contract: ScheduleRunRepository

New data-access layer for `schedule_runs` — `src/lib/repositories/schedule_run_repository.py`.
Follows the existing repository pattern (constructor takes an `AsyncSession`; methods commit).
No raw SQL (SQLAlchemy/SQLModel only).

```python
class ScheduleRunRepository:
    def __init__(self, session: AsyncSession) -> None: ...

    async def create_in_progress(self, run: RunStart) -> ScheduleRun:
        """Insert an in_progress row keyed by run_id and commit. Returns the row."""

    async def mark_success(self, run_id: str, result: dict[str, Any]) -> ScheduleRun | None:
        """Set status=success, end_time=now(UTC), result. Return None if no row for run_id."""

    async def mark_error(self, run_id: str, error_text: str) -> ScheduleRun | None:
        """Set status=error, end_time=now(UTC), result={'error_text': error_text}.
        Return None if no row for run_id."""
```

`RunStart` is a small Pydantic model grouping the create inputs (keeps params ≤2–3,
Constitution V):

```python
class RunStart(BaseModel):
    run_id: str
    schedule_id: str
    schedule_snapshot: dict[str, Any]
```

## Behavior

| Method | Reads | Writes | Returns |
|--------|-------|--------|---------|
| `create_in_progress` | — | INSERT `id=UUID(run_id)`, `status="in_progress"`, `end_time=NULL`, `result=NULL`, `schedule_id`, `schedule_snapshot` | inserted `ScheduleRun` |
| `mark_success` | `session.get(ScheduleRun, UUID(run_id))` | UPDATE `status="success"`, `end_time=now(UTC)`, `result=<dict>` | row, or `None` if absent |
| `mark_error` | `session.get(ScheduleRun, UUID(run_id))` | UPDATE `status="error"`, `end_time=now(UTC)`, `result={"error_text": ...}` | row, or `None` if absent |

- `status` values come from `ScheduleRunStatus` (`.value`), matching the DB CHECK constraint.
- `run_id` (str) is converted to `uuid.UUID` at the boundary; the row PK equals this UUID.
- Each method commits its own transaction. A missing row on a terminal update returns `None`
  (the caller logs the discrepancy — FR-010); methods do not raise for "not found".
- `end_time` uses timezone-aware `datetime.now(UTC)`.

## Session ownership

The worker callers open the session: `async with Database().sessionmaker() as session:` →
`ScheduleRunRepository(session)`. The repository does not create or own engines/sessions.

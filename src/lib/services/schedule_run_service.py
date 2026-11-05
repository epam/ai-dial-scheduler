from src.lib.exceptions.errors import ScheduleAlreadyDeletedError, ScheduleNotFoundError, ScheduleRunNotFoundError
from src.lib.models.schedule_run import ScheduleRun
from src.lib.repositories.schedule_repository import ScheduleRepository
from src.lib.repositories.schedule_run_repository import ScheduleRunRepository
from src.lib.schemas.ordering import RunOrdering
from src.lib.schemas.pagination import Page, Paginator
from src.lib.schemas.run import RunListItem, RunResponse, RunScheduleSummary
from src.lib.utils.logging import logger


class ScheduleRunService:
    """Business logic for schedule run access and on-demand execution, with ownership enforcement."""

    def __init__(self, schedule_repo: ScheduleRepository, run_repo: ScheduleRunRepository) -> None:
        self._schedule_repo = schedule_repo
        self._run_repo = run_repo

    async def create_on_demand_run(self, schedule_id: str, created_by: str) -> ScheduleRun:
        """Record an ``in_progress`` run for an owned schedule, to be executed in the background.

        Only the stored definition is used: the run's snapshot is the schedule's saved
        ``kwargs``, exactly as a scheduled fire would record it, so an on-demand run and a
        scheduled one are indistinguishable afterwards. The scheduler is not touched, so the
        schedule's next fire time is unaffected.

        A run in progress is not a barrier — a schedule may have several at once.

        Raises:
            ScheduleNotFoundError: The schedule does not exist or belongs to someone else.
            ScheduleAlreadyDeletedError: The schedule is soft-deleted and can never run again.
        """
        row = await self._schedule_repo.get(schedule_id, created_by)
        if row is None:
            raise ScheduleNotFoundError(schedule_id)
        schedule, _ = row
        if schedule.is_deleted:
            raise ScheduleAlreadyDeletedError(schedule_id)

        run = await self._run_repo.create_in_progress(schedule_id=schedule_id, schedule_snapshot=dict(schedule.kwargs))
        logger.info("on_demand_run_created", schedule_id=schedule_id, run_id=str(run.id), created_by=created_by)
        return run

    async def list_runs(
        self,
        schedule_id: str,
        created_by: str,
        paginator: Paginator,
        ordering: RunOrdering | None = None,
    ) -> Page[RunListItem]:
        """Return paginated runs for an owned schedule, or raise ScheduleNotFoundError.

        ``ordering`` controls sort order (defaults to ``created_at`` descending) and is
        echoed into the ``next``/``previous`` page links so it survives pagination.
        """
        row = await self._schedule_repo.get(schedule_id, created_by)
        if row is None:
            raise ScheduleNotFoundError(schedule_id)

        ordering = ordering or RunOrdering()
        paginator.extra_query = ordering.to_query()

        logger.info("Listing runs", schedule_id=schedule_id, created_by=created_by)

        total, runs = await self._run_repo.list_for_schedule(
            schedule_id, limit=paginator.limit, offset=paginator.offset, ordering=ordering
        )
        results = [
            RunListItem(
                id=run.id,
                status=run.status,
                start_time=run.created_at,
                end_time=run.end_time,
                conversation_id=run.conversation_id,
            )
            for run in runs
        ]
        return paginator.paginate(count=total, results=results)

    async def get_run(self, schedule_id: str, created_by: str, run_id: str) -> RunResponse:
        """Return full run detail for an owned schedule, or raise ScheduleNotFoundError / ScheduleRunNotFoundError."""
        row = await self._schedule_repo.get(schedule_id, created_by)
        if row is None:
            raise ScheduleNotFoundError(schedule_id)
        schedule, _ = row

        logger.info("Fetching run", schedule_id=schedule_id, run_id=run_id, created_by=created_by)

        run = await self._run_repo.get_for_schedule(schedule_id, run_id)
        if run is None:
            raise ScheduleRunNotFoundError(run_id)

        return RunResponse(
            id=run.id,
            status=run.status,
            start_time=run.created_at,
            end_time=run.end_time,
            result=run.result,
            schedule=RunScheduleSummary(
                display_name=schedule.display_name,
                trigger=schedule.trigger,
                properties=schedule.kwargs,
            ),
            conversation_id=run.conversation_id,
        )

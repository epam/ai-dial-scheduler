from dataclasses import dataclass

from fastapi import Query

from src.lib.db.enums import RunSortField, ScheduleSortField, SortDirection
from src.lib.schemas.ordering import RunOrdering, ScheduleOrdering


@dataclass
class ScheduleOrderingParams:
    """FastAPI query dependency for schedule list ordering.

    Invalid ``order_by``/``order_dir`` values are rejected by FastAPI with HTTP 422.
    Defaults reproduce the historical ``created_at`` descending order.
    """

    order_by: ScheduleSortField = Query(default=ScheduleSortField.CREATED_AT)
    order_dir: SortDirection = Query(default=SortDirection.DESC)

    def ordering(self) -> ScheduleOrdering:
        return ScheduleOrdering(order_by=self.order_by, order_dir=self.order_dir)


@dataclass
class RunOrderingParams:
    """FastAPI query dependency for run list ordering (``created_at`` only).

    ``next_run_time``/``name`` are not valid fields and are rejected with HTTP 422.
    """

    order_by: RunSortField = Query(default=RunSortField.CREATED_AT)
    order_dir: SortDirection = Query(default=SortDirection.DESC)

    def ordering(self) -> RunOrdering:
        return RunOrdering(order_by=self.order_by, order_dir=self.order_dir)

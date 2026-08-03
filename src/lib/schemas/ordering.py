from pydantic import BaseModel

from src.lib.db.enums import RunSortField, ScheduleSortField, SortDirection


class ScheduleOrdering(BaseModel):
    """Ordering value object for the schedules list.

    Defaults reproduce the historical behavior (``created_at`` descending) so the
    parameter is additive. Field values are validated against their enums, so invalid
    input raises ``pydantic.ValidationError`` (surfaced as HTTP 422 by FastAPI).
    """

    order_by: ScheduleSortField = ScheduleSortField.CREATED_AT
    order_dir: SortDirection = SortDirection.DESC

    def to_query(self) -> str:
        """Render the ordering as a URL query fragment (no leading ``&``)."""
        return f"order_by={self.order_by.value}&order_dir={self.order_dir.value}"


class RunOrdering(BaseModel):
    """Ordering value object for the runs list (``created_at`` only)."""

    order_by: RunSortField = RunSortField.CREATED_AT
    order_dir: SortDirection = SortDirection.DESC

    def to_query(self) -> str:
        """Render the ordering as a URL query fragment (no leading ``&``)."""
        return f"order_by={self.order_by.value}&order_dir={self.order_dir.value}"

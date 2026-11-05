from pydantic import ValidationError
import pytest

from src.lib.db.enums import RunSortField, ScheduleSortField, SortDirection
from src.lib.schemas.ordering import RunOrdering, ScheduleOrdering


class TestScheduleOrdering:
    def test_defaults_to_created_at_desc(self):
        ordering = ScheduleOrdering()
        assert ordering.order_by is ScheduleSortField.CREATED_AT
        assert ordering.order_dir is SortDirection.DESC

    def test_parses_valid_values(self):
        ordering = ScheduleOrdering(order_by="next_run_time", order_dir="asc")
        assert ordering.order_by is ScheduleSortField.NEXT_RUN_TIME
        assert ordering.order_dir is SortDirection.ASC

    def test_accepts_name_field(self):
        ordering = ScheduleOrdering(order_by="name", order_dir="desc")
        assert ordering.order_by is ScheduleSortField.NAME

    def test_rejects_invalid_order_by(self):
        with pytest.raises(ValidationError):
            ScheduleOrdering(order_by="bogus")

    def test_rejects_invalid_order_dir(self):
        with pytest.raises(ValidationError):
            ScheduleOrdering(order_dir="sideways")

    def test_to_query_emits_order_params(self):
        ordering = ScheduleOrdering(order_by="name", order_dir="asc")
        assert ordering.to_query() == "order_by=name&order_dir=asc"

    def test_to_query_defaults(self):
        assert ScheduleOrdering().to_query() == "order_by=created_at&order_dir=desc"


class TestRunOrdering:
    def test_defaults_to_created_at_desc(self):
        ordering = RunOrdering()
        assert ordering.order_by is RunSortField.CREATED_AT
        assert ordering.order_dir is SortDirection.DESC

    def test_parses_valid_values(self):
        ordering = RunOrdering(order_by="created_at", order_dir="asc")
        assert ordering.order_dir is SortDirection.ASC

    def test_rejects_next_run_time(self):
        with pytest.raises(ValidationError):
            RunOrdering(order_by="next_run_time")

    def test_rejects_name(self):
        with pytest.raises(ValidationError):
            RunOrdering(order_by="name")

    def test_to_query_emits_order_params(self):
        assert RunOrdering(order_by="created_at", order_dir="asc").to_query() == "order_by=created_at&order_dir=asc"

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from src.lib.db.enums import ScheduleRunStatus
from src.lib.models.schedule_run import ScheduleRun


class TestScheduleRunModel:
    def test_tablename(self) -> None:
        assert ScheduleRun.__tablename__ == "schedule_runs"

    def test_id_is_primary_key(self) -> None:
        col = ScheduleRun.__table__.c.id
        assert col.primary_key is True

    def test_id_not_nullable(self) -> None:
        assert not ScheduleRun.__table__.c.id.nullable

    def test_end_time_is_nullable(self) -> None:
        assert ScheduleRun.__table__.c.end_time.nullable

    def test_status_not_nullable(self) -> None:
        assert not ScheduleRun.__table__.c.status.nullable

    def test_result_is_jsonb_nullable(self) -> None:
        col = ScheduleRun.__table__.c.result
        assert isinstance(col.type, JSONB)
        assert col.nullable

    def test_schedule_id_is_varchar_191_not_nullable(self) -> None:
        col = ScheduleRun.__table__.c.schedule_id
        assert isinstance(col.type, sa.String)
        assert col.type.length == 191
        assert not col.nullable

    def test_schedule_snapshot_is_jsonb_not_nullable(self) -> None:
        col = ScheduleRun.__table__.c.schedule_snapshot
        assert isinstance(col.type, JSONB)
        assert not col.nullable

    def test_status_check_constraint_covers_all_enum_values(self) -> None:
        check_constraints = [c for c in ScheduleRun.__table__.constraints if isinstance(c, sa.CheckConstraint)]
        assert len(check_constraints) == 1
        text = str(check_constraints[0].sqltext).lower()
        for status in ScheduleRunStatus:
            assert status.value in text

    def test_fk_to_schedules_with_restrict(self) -> None:
        fk_constraints = [c for c in ScheduleRun.__table__.constraints if isinstance(c, sa.ForeignKeyConstraint)]
        assert len(fk_constraints) == 1
        fk = fk_constraints[0]
        assert any(elem.ondelete.upper() == "RESTRICT" for elem in fk.elements)

    def test_index_on_schedule_id_exists(self) -> None:
        index_names = {idx.name for idx in ScheduleRun.__table__.indexes}
        assert "IX_schedule_runs_schedule_id" in index_names

    def test_check_constraint_named_correctly(self) -> None:
        check_constraints = [c for c in ScheduleRun.__table__.constraints if isinstance(c, sa.CheckConstraint)]
        assert len(check_constraints) == 1
        # Naming convention: CK_%(table_name)s_%(constraint_name)s → CK_schedule_runs_status
        assert check_constraints[0].name == "CK_schedule_runs_status"

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from src.lib.models.schedule import Schedule


class TestScheduleModel:
    def test_tablename(self) -> None:
        assert Schedule.__tablename__ == "schedules"

    def test_id_column_is_varchar_191(self) -> None:
        col = Schedule.__table__.c.id
        assert isinstance(col.type, sa.String)
        assert col.type.length == 191

    def test_id_is_primary_key(self) -> None:
        assert Schedule.__table__.c.id.primary_key is True

    def test_id_not_nullable(self) -> None:
        assert not Schedule.__table__.c.id.nullable

    def test_display_name_not_nullable(self) -> None:
        assert not Schedule.__table__.c.display_name.nullable

    def test_created_by_not_nullable(self) -> None:
        assert not Schedule.__table__.c.created_by.nullable

    def test_trigger_is_jsonb(self) -> None:
        assert isinstance(Schedule.__table__.c.trigger.type, JSONB)

    def test_trigger_not_nullable(self) -> None:
        assert not Schedule.__table__.c.trigger.nullable

    def test_kwargs_is_jsonb(self) -> None:
        assert isinstance(Schedule.__table__.c.kwargs.type, JSONB)

    def test_kwargs_not_nullable(self) -> None:
        assert not Schedule.__table__.c.kwargs.nullable

    def test_created_at_inherits_from_base(self) -> None:
        assert "created_at" in Schedule.__table__.c

    def test_updated_at_inherits_from_base(self) -> None:
        assert "updated_at" in Schedule.__table__.c

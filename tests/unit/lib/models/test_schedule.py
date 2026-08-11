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

    def test_service_id_not_nullable(self) -> None:
        assert not Schedule.__table__.c.service_id.nullable

    def test_trigger_is_jsonb(self) -> None:
        assert isinstance(Schedule.__table__.c.trigger.type, JSONB)

    def test_trigger_not_nullable(self) -> None:
        assert not Schedule.__table__.c.trigger.nullable

    def test_kwargs_is_jsonb(self) -> None:
        assert isinstance(Schedule.__table__.c.kwargs.type, JSONB)

    def test_kwargs_not_nullable(self) -> None:
        assert not Schedule.__table__.c.kwargs.nullable

    def test_metadata_column_named_metadata(self) -> None:
        assert "metadata" in Schedule.__table__.c

    def test_metadata_is_jsonb(self) -> None:
        assert isinstance(Schedule.__table__.c["metadata"].type, JSONB)

    def test_metadata_not_nullable(self) -> None:
        assert not Schedule.__table__.c["metadata"].nullable

    def test_metadata_has_server_default(self) -> None:
        assert Schedule.__table__.c["metadata"].server_default is not None

    def test_metadata_gin_index_declared(self) -> None:
        index = next(ix for ix in Schedule.__table__.indexes if ix.name == "IX_schedules_metadata")
        assert index.dialect_options["postgresql"]["using"] == "gin"
        assert [c.name for c in index.columns] == ["metadata"]

    def test_metadata_attribute_defaults_to_empty_dict(self) -> None:
        schedule = Schedule(
            id="x",
            display_name="x",
            created_by="u",
            service_id="dial-oauth",
            trigger={},
            kwargs={},
        )
        assert schedule.schedule_metadata == {}

    def test_is_deleted_is_boolean(self) -> None:
        assert isinstance(Schedule.__table__.c.is_deleted.type, sa.Boolean)

    def test_is_deleted_not_nullable(self) -> None:
        assert not Schedule.__table__.c.is_deleted.nullable

    def test_is_deleted_has_false_server_default(self) -> None:
        server_default = Schedule.__table__.c.is_deleted.server_default
        assert server_default is not None
        assert "false" in str(server_default.arg).lower()

    def test_is_deleted_not_indexed(self) -> None:
        indexed = {c.name for ix in Schedule.__table__.indexes for c in ix.columns}
        assert "is_deleted" not in indexed

    def test_is_deleted_attribute_defaults_to_false(self) -> None:
        schedule = Schedule(
            id="x",
            display_name="x",
            created_by="u",
            service_id="dial-oauth",
            trigger={},
            kwargs={},
        )
        assert schedule.is_deleted is False

    def test_created_at_inherits_from_base(self) -> None:
        assert "created_at" in Schedule.__table__.c

    def test_updated_at_inherits_from_base(self) -> None:
        assert "updated_at" in Schedule.__table__.c

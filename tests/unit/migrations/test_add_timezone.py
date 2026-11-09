"""Guard the add_timezone_to_schedules migration's upgrade/downgrade behaviour.

The migration is loaded from its file (its module name starts with a digit) and its
``alembic.op`` calls are recorded to assert a single ``ADD COLUMN`` that is NOT NULL with a
``'UTC'`` server default — which backfills pre-existing rows in the same statement, so they
keep firing at the same UTC instants — and a matching ``DROP COLUMN`` on downgrade.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "versions"
    / "20261104_1200_c8d6f7a9b0e1_add_timezone_to_schedules.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_add_timezone_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration(mocker):
    module = _load_migration()
    mocker.patch.object(module, "op")
    return module


def test_revision_chains_to_model_column_migration():
    module = _load_migration()
    assert module.revision == "c8d6f7a9b0e1"
    assert module.down_revision == "b7c5e6f8a9d0"


def test_upgrade_adds_not_null_string_with_utc_server_default(migration):
    migration.upgrade()

    migration.op.add_column.assert_called_once()
    table, column = migration.op.add_column.call_args.args
    assert table == "schedules"
    assert column.name == "timezone"
    assert isinstance(column.type, sa.String)
    assert column.type.length == 64
    assert column.nullable is False
    assert str(column.server_default.arg) == "'UTC'"


def test_upgrade_needs_no_separate_backfill(migration):
    """A NOT NULL column with a server default backfills existing rows in the ADD COLUMN itself."""
    migration.upgrade()

    migration.op.execute.assert_not_called()
    migration.op.alter_column.assert_not_called()


def test_downgrade_drops_column(migration):
    migration.downgrade()

    migration.op.drop_column.assert_called_once_with("schedules", "timezone")

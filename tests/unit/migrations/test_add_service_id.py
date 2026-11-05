"""Guard the add_service_id_to_schedules migration's upgrade/downgrade behaviour.

The migration is loaded from its file (its module name starts with a digit) and
its ``alembic.op`` calls are recorded to assert: add nullable column → backfill
pre-existing rows with the ``dial-oauth`` literal → enforce NOT NULL.
"""

import importlib.util
from pathlib import Path

import pytest


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "versions"
    / "20260714_1000_c3f1a2b4d5e6_add_service_id_to_schedules.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_add_service_id_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration(mocker):
    module = _load_migration()
    mocker.patch.object(module, "op")
    return module


def test_revision_chains_to_metadata_migration():
    module = _load_migration()
    assert module.revision == "c3f1a2b4d5e6"
    assert module.down_revision == "be7408b25155"


def test_upgrade_adds_column_backfills_then_sets_not_null(migration):
    migration.upgrade()

    migration.op.add_column.assert_called_once()
    add_args = migration.op.add_column.call_args.args
    assert add_args[0] == "schedules"

    execute_sql = migration.op.execute.call_args.args[0]
    assert "service_id" in execute_sql
    assert "dial-oauth" in execute_sql

    migration.op.alter_column.assert_called_once()
    assert migration.op.alter_column.call_args.kwargs["nullable"] is False


def test_downgrade_drops_column(migration):
    migration.downgrade()

    migration.op.drop_column.assert_called_once_with("schedules", "service_id")

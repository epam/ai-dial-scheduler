"""Guard the set_service_id_to_dial_native migration's upgrade/downgrade behaviour.

The migration is loaded from its file (its module name starts with a digit) and
its ``alembic.op`` calls are recorded to assert: upgrade backfills every row's
``service_id`` to ``dial-native``; downgrade reverts to ``dial-oauth``.
"""

import importlib.util
from pathlib import Path

import pytest


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "versions"
    / "20260814_1200_f6a4d5e7b8c9_set_service_id_to_dial_native.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_set_service_id_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration(mocker):
    module = _load_migration()
    mocker.patch.object(module, "op")
    return module


def test_revision_chains_to_is_deleted_migration():
    module = _load_migration()
    assert module.revision == "f6a4d5e7b8c9"
    assert module.down_revision == "e5f3c4d6a7b8"


def test_upgrade_backfills_all_rows_to_dial_native(migration):
    migration.upgrade()

    migration.op.execute.assert_called_once()
    sql = migration.op.execute.call_args.args[0]
    assert "UPDATE schedules" in sql
    assert "service_id" in sql
    assert "dial-native" in sql
    assert "WHERE" not in sql


def test_downgrade_reverts_to_dial_oauth(migration):
    migration.downgrade()

    migration.op.execute.assert_called_once()
    sql = migration.op.execute.call_args.args[0]
    assert "UPDATE schedules" in sql
    assert "service_id" in sql
    assert "dial-oauth" in sql

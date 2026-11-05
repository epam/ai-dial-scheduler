from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from src.lib.db.sync_engine import build_sync_engine
from src.lib.settings import DatabaseSettings


def build_jobstore(db_settings: DatabaseSettings) -> SQLAlchemyJobStore:
    """Build the APScheduler job store backed by a sync engine.

    The engine is built via :func:`build_sync_engine` which knows how to configure itself for
    every supported auth mode (password, connection-string, gcp_iam, azure_entra). Passing a
    pre-built engine rather than a URL keeps a single construction path across all modes.
    """
    return SQLAlchemyJobStore(engine=build_sync_engine(db_settings))

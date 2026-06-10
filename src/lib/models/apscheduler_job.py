"""Read-only SQLAlchemy Core Table reference for the APScheduler job store.

This table is owned and created by APScheduler's SQLAlchemyJobStore.
It is declared on a separate MetaData so Alembic autogenerate never emits
DDL for it, regardless of the include_name guard in migrations/env.py.
Only the columns needed for the JOIN are declared.
"""

from sqlalchemy import Column, Double, MetaData, String, Table


_unmanaged_metadata = MetaData()

apscheduler_jobs = Table(
    "apscheduler_jobs",
    _unmanaged_metadata,
    Column("id", String(191), primary_key=True, nullable=False),
    Column("next_run_time", Double(), nullable=True),
)

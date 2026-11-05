"""add service_id to schedules

Revision ID: c3f1a2b4d5e6
Revises: be7408b25155
Create Date: 2026-07-14 10:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3f1a2b4d5e6"
down_revision: Union[str, Sequence[str], None] = "be7408b25155"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add service_id, backfill pre-existing rows, then enforce NOT NULL.

    The backfill value ``dial-oauth`` is applied only to rows that existed before
    this feature; it is intentionally hardcoded here (a migration is a historical
    snapshot) and is NOT a default for newly created schedules.
    """
    op.add_column("schedules", sa.Column("service_id", sa.String(), nullable=True))
    op.execute("UPDATE schedules SET service_id = 'dial-oauth' WHERE service_id IS NULL")
    op.alter_column("schedules", "service_id", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("schedules", "service_id")

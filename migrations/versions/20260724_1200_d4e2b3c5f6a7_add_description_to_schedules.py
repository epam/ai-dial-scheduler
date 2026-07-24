"""add description to schedules

Revision ID: d4e2b3c5f6a7
Revises: c3f1a2b4d5e6
Create Date: 2026-07-24 12:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e2b3c5f6a7"
down_revision: Union[str, Sequence[str], None] = "c3f1a2b4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the optional description column.

    Nullable with no server default and no backfill: pre-existing rows keep NULL,
    which the response layer normalizes to an absent/empty description. Not indexed
    (description is neither filtered nor sorted).
    """
    op.add_column("schedules", sa.Column("description", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("schedules", "description")

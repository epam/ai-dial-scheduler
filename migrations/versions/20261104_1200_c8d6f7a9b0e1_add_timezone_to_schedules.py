"""add timezone to schedules

Revision ID: c8d6f7a9b0e1
Revises: b7c5e6f8a9d0
Create Date: 2026-11-04 12:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8d6f7a9b0e1"
down_revision: Union[str, Sequence[str], None] = "b7c5e6f8a9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the IANA timezone in which a schedule's trigger is evaluated.

    NOT NULL with a ``'UTC'`` server default, which backfills every pre-existing row in the same
    statement (Postgres 11+ does this without a table rewrite). Every schedule created before this
    column was evaluated in UTC, so the backfilled value preserves their fire times exactly and no
    separate data migration is required. The server default is retained permanently so direct SQL
    inserts stay valid. VARCHAR(64) leaves headroom over the longest IANA name (32 characters).
    Not indexed: nothing filters or sorts by timezone.
    """
    op.add_column(
        "schedules",
        sa.Column("timezone", sa.String(64), nullable=False, server_default=sa.text("'UTC'")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("schedules", "timezone")

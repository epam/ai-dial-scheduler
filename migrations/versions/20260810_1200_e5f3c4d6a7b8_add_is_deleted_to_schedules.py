"""add is_deleted to schedules

Revision ID: e5f3c4d6a7b8
Revises: d4e2b3c5f6a7
Create Date: 2026-08-10 12:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5f3c4d6a7b8"
down_revision: Union[str, Sequence[str], None] = "d4e2b3c5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the soft-delete marker column.

    NOT NULL with a ``false`` server default, which backfills every pre-existing row in the
    same statement (Postgres 11+ does this without a table rewrite), so no separate data
    migration is required: nothing has been deleted yet. The server default is retained
    permanently so direct SQL inserts stay valid. Not indexed — the column is a two-valued
    boolean and the more selective ``created_by`` it is filtered alongside is itself unindexed.
    """
    op.add_column(
        "schedules",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("schedules", "is_deleted")

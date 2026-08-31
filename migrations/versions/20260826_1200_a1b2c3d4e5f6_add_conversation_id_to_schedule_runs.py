"""add conversation_id to schedule_runs

Revision ID: a1b2c3d4e5f6
Revises: f6a4d5e7b8c9
Create Date: 2026-08-26 12:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f6a4d5e7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable conversation_id column to schedule_runs.

    Unbounded VARCHAR (no length cap) because the conversation id is a storage path whose
    length is not bounded. Nullable with no server default: SQL NULL is the correct
    representation of "no conversation was created", and pre-existing rows are left NULL
    automatically. No index and no FK: the runs list filters by the already-indexed
    schedule_id and the column does not reference any scheduler-owned table.
    """
    op.add_column(
        "schedule_runs",
        sa.Column("conversation_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("schedule_runs", "conversation_id")

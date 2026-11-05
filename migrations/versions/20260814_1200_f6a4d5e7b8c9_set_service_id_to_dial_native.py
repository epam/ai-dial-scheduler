"""set service_id to dial-native for all schedules

Revision ID: f6a4d5e7b8c9
Revises: e5f3c4d6a7b8
Create Date: 2026-08-14 12:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f6a4d5e7b8c9"
down_revision: Union[str, Sequence[str], None] = "e5f3c4d6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill every schedule's ``service_id`` to ``dial-native``.

    A pure data migration: the ``service_id`` column is unchanged. The value
    ``dial-native`` is hardcoded here because a migration is a historical
    snapshot and is NOT a default for newly created schedules.
    """
    op.execute("UPDATE schedules SET service_id = 'dial-native'")


def downgrade() -> None:
    """Revert ``service_id`` to the prior backfill value ``dial-oauth``.

    Per-row original values are not recoverable, so all rows are reset to the
    value the ``add_service_id`` migration originally backfilled.
    """
    op.execute("UPDATE schedules SET service_id = 'dial-oauth'")

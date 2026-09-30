"""add generated model column to schedules

Revision ID: b7c5e6f8a9d0
Revises: a1b2c3d4e5f6
Create Date: 2026-09-29 12:00:00.000000+00:00

Projects ``kwargs -> payload -> model`` into its own column so the schedules list can show
and filter by model without reading the whole ``kwargs`` document.

The column is ``GENERATED ALWAYS ... STORED``: PostgreSQL maintains it on every insert and
update, so it can never disagree with ``kwargs``, and it is not writable by the application.
It is NULL whenever the payload has no string ``model`` — REST and SSE targets, or a payload
where ``model`` is absent or JSON null — which is exactly what "no model" means to the API.

Why a column instead of an expression index: filtering on the raw JSONB path forces PostgreSQL
to detoast every candidate row, which measured ~195 ms per list request on 50k schedules with
long chat histories (~290 MB of TOAST). The stored column removes the detoast entirely and
brings the same query to ~25 ms, without needing any extension (``pg_trgm`` is deliberately
avoided: ``CREATE EXTENSION`` needs privileges the workload-identity database users on GCP,
Azure and AWS are not guaranteed to have).

Note: adding a STORED generated column rewrites the table and holds an ACCESS EXCLUSIVE lock
for the duration. On this table (one row per schedule) that is short, but it is not an online
operation.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from src.lib.models.schedule import MODEL_GENERATION_EXPRESSION


revision: str = "b7c5e6f8a9d0"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column(
            "model",
            sa.String(),
            sa.Computed(MODEL_GENERATION_EXPRESSION, persisted=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("schedules", "model")

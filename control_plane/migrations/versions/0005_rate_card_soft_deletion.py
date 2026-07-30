"""Preserve historical usage while allowing administrators to delete rate cards.

Revision ID: 0005_rate_card_soft_deletion
Revises: 0004_managed_provider_configuration
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_rate_card_soft_deletion"
down_revision = "0004_managed_provider_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("rate_cards")}
    if "deleted_at" not in columns:
        op.add_column(
            "rate_cards",
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("rate_cards")}
    if "deleted_at" in columns:
        # SQLite versions before 3.35 do not support ALTER TABLE ... DROP COLUMN.
        # Alembic's batch mode rebuilds the table and works on both older
        # production SQLite releases and newer development environments.
        with op.batch_alter_table("rate_cards") as batch_op:
            batch_op.drop_column("deleted_at")

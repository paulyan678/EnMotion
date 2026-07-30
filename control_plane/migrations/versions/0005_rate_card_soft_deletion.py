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
        op.drop_column("rate_cards", "deleted_at")

"""Make desktop release capability phases one-time.

Revision ID: 0003_one_time_release_grants
Revises: 0002_release_grants
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_one_time_release_grants"
down_revision = "0002_release_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "release_grants",
        sa.Column("manifest_consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "release_grants",
        sa.Column("download_consumed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("release_grants", "download_consumed_at")
    op.drop_column("release_grants", "manifest_consumed_at")

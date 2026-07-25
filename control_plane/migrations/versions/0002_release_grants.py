"""Add private desktop release capability grants.

Revision ID: 0002_release_grants
Revises: 0001_control_plane
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_release_grants"
down_revision = "0001_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "release_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("target", sa.String(length=16), nullable=False),
        sa.Column("arch", sa.String(length=16), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("current_version", sa.String(length=120), nullable=False),
        sa.Column("release_version", sa.String(length=120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index(
        "ix_release_grants_user_expires",
        "release_grants",
        ["user_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_release_grants_user_expires", table_name="release_grants")
    op.drop_table("release_grants")

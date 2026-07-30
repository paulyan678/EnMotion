"""Add encrypted, versioned provider configuration.

Revision ID: 0004_managed_provider_configuration
Revises: 0003_one_time_release_grants
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_managed_provider_configuration"
down_revision = "0003_one_time_release_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_configurations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("provider_base_url", sa.Text(), nullable=False),
        sa.Column("credentials_nonce", sa.String(length=32), nullable=False),
        sa.Column("credentials_ciphertext", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "version",
            name="uq_provider_configurations_version",
        ),
    )
    op.create_index(
        "ix_provider_configurations_created",
        "provider_configurations",
        ["created_at"],
        unique=False,
    )
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("provider_tasks")}
    if "provider_config_version" not in columns:
        op.add_column(
            "provider_tasks",
            sa.Column("provider_config_version", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("provider_tasks")}
    if "provider_config_version" in columns:
        # Batch mode rebuilds the table on SQLite versions that predate native
        # DROP COLUMN support.
        with op.batch_alter_table("provider_tasks") as batch_op:
            batch_op.drop_column("provider_config_version")
    op.drop_index(
        "ix_provider_configurations_created",
        table_name="provider_configurations",
    )
    op.drop_table("provider_configurations")

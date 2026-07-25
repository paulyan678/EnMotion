"""Create durable generation jobs.

Revision ID: 0002_generation_jobs
Revises: 0001_server_identity
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_generation_jobs"
down_revision = "0001_server_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("queue_task_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_generation_jobs_progress",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'canceled')",
            name="ck_generation_jobs_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generation_jobs_workspace_created",
        "generation_jobs",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_generation_jobs_status_created",
        "generation_jobs",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_status_created", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_workspace_created", table_name="generation_jobs")
    op.drop_table("generation_jobs")

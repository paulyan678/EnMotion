"""Persist generation progress stages and timelines.

Revision ID: 0004_job_activity
Revises: 0003_job_retry_context
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_job_activity"
down_revision = "0003_job_retry_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("progress_stage", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "progress_is_estimated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("progress_steps", sa.JSON(), nullable=True),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("provider_progress", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generation_jobs", "provider_progress")
    op.drop_column("generation_jobs", "progress_steps")
    op.drop_column("generation_jobs", "progress_is_estimated")
    op.drop_column("generation_jobs", "progress_stage")

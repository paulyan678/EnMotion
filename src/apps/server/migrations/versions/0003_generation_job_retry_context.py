"""Persist failed-state snapshots while generation jobs are retried.

Revision ID: 0003_job_retry_context
Revises: 0002_generation_jobs
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_job_retry_context"
down_revision = "0002_generation_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("retry_context", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generation_jobs", "retry_context")

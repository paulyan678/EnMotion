"""Initial EnMotion control-plane schema.

Revision ID: 0001_control_plane
Revises:
Create Date: 2026-07-24
"""

from alembic import op

from app.database import Base
from app import models  # noqa: F401


revision = "0001_control_plane"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(
        bind=op.get_bind(),
        tables=[
            models.User.__table__,
            models.LoginSession.__table__,
            models.RateCard.__table__,
            models.UsageRequest.__table__,
            models.ProviderTask.__table__,
            models.CreditLedger.__table__,
            models.AuditEvent.__table__,
        ],
    )


def downgrade() -> None:
    Base.metadata.drop_all(
        bind=op.get_bind(),
        tables=[
            models.AuditEvent.__table__,
            models.CreditLedger.__table__,
            models.ProviderTask.__table__,
            models.UsageRequest.__table__,
            models.RateCard.__table__,
            models.LoginSession.__table__,
            models.User.__table__,
        ],
    )

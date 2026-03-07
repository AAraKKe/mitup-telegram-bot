"""Add duration and lock fields to meetups and settings

Revision ID: b217be05bdbf
Revises: 4ae62a893c93
Create Date: 2026-03-06 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b217be05bdbf"
down_revision: str | None = "4ae62a893c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meetups", sa.Column("duration_minutes", sa.Integer, nullable=True))
    op.add_column(
        "meetups", sa.Column("started_notification_sent", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("meetups", sa.Column("lock_on_start", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(
        "settings", sa.Column("meeting_duration_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "settings", sa.Column("default_lock_on_start", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    op.drop_column("meetups", "duration_minutes")
    op.drop_column("meetups", "started_notification_sent")
    op.drop_column("meetups", "lock_on_start")
    op.drop_column("settings", "meeting_duration_enabled")
    op.drop_column("settings", "default_lock_on_start")

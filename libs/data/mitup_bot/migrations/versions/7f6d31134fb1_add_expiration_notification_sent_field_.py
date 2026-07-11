"""Add expiration notification sent field to meetups

Revision ID: 7f6d31134fb1
Revises: 64e7b446b0ac
Create Date: 2025-10-18 23:24:23.501309+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "7f6d31134fb1"
down_revision: str | None = "64e7b446b0ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.add_column(
        "meetups", sa.Column("expiration_notification_sent", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade():
    op.drop_column("meetups", "expiration_notification_sent")

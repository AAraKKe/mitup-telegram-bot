"""replace duration_minutes with end_datetime

Revision ID: a1b2c3d4e5f6
Revises: cd455e4de6dc
Create Date: 2026-03-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "cd455e4de6dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meetups", sa.Column("end_datetime", sa.DateTime(), nullable=True))

    # Convert existing duration_minutes data into end_datetime
    op.execute(
        """
        UPDATE meetups
        SET end_datetime = datetime + (duration_minutes || ' minutes')::interval
        WHERE duration_minutes IS NOT NULL AND datetime IS NOT NULL
        """
    )

    op.drop_column("meetups", "duration_minutes")


def downgrade() -> None:
    op.add_column("meetups", sa.Column("duration_minutes", sa.Integer(), nullable=True))

    # Convert end_datetime back to duration_minutes (in whole minutes)
    op.execute(
        """
        UPDATE meetups
        SET duration_minutes = EXTRACT(EPOCH FROM (end_datetime - datetime))::int / 60
        WHERE end_datetime IS NOT NULL AND datetime IS NOT NULL
        """
    )

    op.drop_column("meetups", "end_datetime")

"""Replace date and time by datetime column

Revision ID: 8c24a14289b5
Revises: c2f2c0bc4ecf
Create Date: 2024-05-06 19:42:59.646703+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c24a14289b5"
down_revision: str | None = "c2f2c0bc4ecf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.drop_column("meetups", "date")
    op.drop_column("meetups", "time")
    op.add_column("meetups", sa.Column("datetime", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("meetups", "datetime")
    op.add_column("meetups", sa.Column("time", sa.Time(), nullable=True))
    op.add_column("meetups", sa.Column("date", sa.Date(), nullable=True))

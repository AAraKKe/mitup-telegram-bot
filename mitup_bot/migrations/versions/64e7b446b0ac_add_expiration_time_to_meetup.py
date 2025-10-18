"""Add expiration time to meetup

Revision ID: 64e7b446b0ac
Revises: 316e7347bdd1
Create Date: 2025-10-18 10:32:15.350156+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "64e7b446b0ac"
down_revision: str | None = "316e7347bdd1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meetups", sa.Column("expiration_time", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("meetups", "expiration_time")

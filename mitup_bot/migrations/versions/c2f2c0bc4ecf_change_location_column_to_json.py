"""Change location column to JSON

Revision ID: c2f2c0bc4ecf
Revises: 50dfc8f3a9cc
Create Date: 2024-03-18 14:33:43.290996+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2f2c0bc4ecf"
down_revision: str | None = "50dfc8f3a9cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("meetups", "location", existing_type=sa.String, type_=sa.JSON, postgresql_using="location::jsonb")

    # Update existing rows with empty location to be a JSON object
    meetup = sa.table("meetups", sa.column("location"))
    op.execute(meetup.update().where(meetup.c.location == sa.null()).values(location="{}"))


def downgrade() -> None:
    op.alter_column("meetups", "location", existing_type=sa.JSON, type_=sa.String)

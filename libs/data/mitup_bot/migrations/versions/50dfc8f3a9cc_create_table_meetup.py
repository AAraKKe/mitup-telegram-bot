"""Create table meetup

Revision ID: 50dfc8f3a9cc
Revises: 39a7e73c61da
Create Date: 2024-02-28 10:20:00.108535+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "50dfc8f3a9cc"
down_revision: str | None = "d965e7c79695"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.create_table(
        "meetups",
        sa.Column("id", sa.BigInteger, nullable=False, primary_key=True),
        sa.Column("owner_id", sa.BigInteger, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_time", sa.DateTime, nullable=True),
        sa.Column("updated_time", sa.DateTime, nullable=True),
        sa.Column("date", sa.Date, nullable=True),
        sa.Column("time", sa.Time, nullable=True),
        sa.Column("max_members", sa.Integer, nullable=True),
        sa.Column("language", sa.String, nullable=True),
        sa.Column("location", sa.String, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, default=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
    )
    helpers.add_created_time_trigger("meetups")
    helpers.add_updated_time_trigger("meetups")


def downgrade():
    op.drop_table("meetups")

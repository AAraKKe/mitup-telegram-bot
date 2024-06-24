"""Create joined user table

Revision ID: e1f0fa1fecb0
Revises: 8c24a14289b5
Create Date: 2024-06-23 14:07:53.359289+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mitup_bot.migrations.helpers import add_created_time_trigger, remove_created_time_trigger

# revision identifiers, used by Alembic.
revision: str = "e1f0fa1fecb0"
down_revision: str | None = "8c24a14289b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "joined_users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column(
            "meetup_id",
            sa.Integer,
            sa.ForeignKey("meetups.id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column("created_time", sa.TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("is_waiting_list", sa.Boolean, default=False),
    )
    add_created_time_trigger("joined_users")


def downgrade() -> None:
    op.drop_table("joined_users")
    remove_created_time_trigger("joined_users")

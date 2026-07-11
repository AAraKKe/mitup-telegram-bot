"""Create messages table

Revision ID: 640f660fe984
Revises: e1f0fa1fecb0
Create Date: 2024-06-23 15:15:40.407962+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "640f660fe984"
down_revision: str | None = "e1f0fa1fecb0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("message_id", sa.Integer),
        sa.Column("chat_id", sa.Integer, index=True),
        sa.Column("inline_message_id", sa.String),
        sa.Column("meetup_id", sa.Integer, sa.ForeignKey("meetups.id", ondelete="CASCADE")),
        sa.Column("buttons", sa.JSON, nullable=False),
    )


def downgrade():
    op.drop_table("messages")

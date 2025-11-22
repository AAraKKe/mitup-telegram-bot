"""add_chat_instance_to_message_table

Revision ID: 207d4e7c7df9
Revises: 7f6d31134fb1
Create Date: 2025-11-19 20:17:32.234845+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "207d4e7c7df9"
down_revision: str | None = "7f6d31134fb1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add chat_instance column to messages table
    op.add_column("messages", sa.Column("chat_instance", sa.String(), nullable=True))


def downgrade() -> None:
    # Remove chat_instance column from messages table
    op.drop_column("messages", "chat_instance")

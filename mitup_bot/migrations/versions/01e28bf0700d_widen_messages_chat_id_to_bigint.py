"""widen messages.chat_id and messages.message_id to BIGINT

Telegram chat IDs are now routinely outside the signed 32-bit range
(values above ~2.15B), which makes the existing `INTEGER` column
overflow with `psycopg2.errors.NumericValueOutOfRange`. `message_id`
is widened in the same migration for consistency / future-proofing.

Revision ID: 01e28bf0700d
Revises: b10eabbea399
Create Date: 2026-05-17 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "01e28bf0700d"
down_revision: str | None = "b10eabbea399"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("messages", "chat_id", existing_type=sa.Integer(), type_=sa.BigInteger())
    op.alter_column("messages", "message_id", existing_type=sa.Integer(), type_=sa.BigInteger())


def downgrade() -> None:
    op.alter_column("messages", "message_id", existing_type=sa.BigInteger(), type_=sa.Integer())
    op.alter_column("messages", "chat_id", existing_type=sa.BigInteger(), type_=sa.Integer())

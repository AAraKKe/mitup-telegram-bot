"""add render digest to messages

Gives `messages` the fingerprint of the card payload Telegram last confirmed, so a refresh that
would re-send identical content can skip the `editMessageText` round trip instead of spending a
call to be told the message is not modified.

The column is nullable and NULL carries meaning: nothing has been confirmed for that card yet, so
it is always edited.

Revision ID: 4d77028bd87b
Revises: 56be745b7ded
Create Date: 2026-08-22 00:23:09.180746+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d77028bd87b"
down_revision: str | None = "56be745b7ded"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.add_column("messages", sa.Column("render_digest", sa.String, nullable=True))


def downgrade():
    op.drop_column("messages", "render_digest")

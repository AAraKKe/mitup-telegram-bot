"""Drop show_timezone columns

Revision ID: 4ae62a893c93
Revises: 7b5cead07a89
Create Date: 2026-03-02 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4ae62a893c93"
down_revision: str | None = "7b5cead07a89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.drop_column("meetups", "show_timezone")
    op.drop_column("settings", "default_show_timezone")


def downgrade():
    op.add_column("meetups", sa.Column("show_timezone", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("settings", sa.Column("default_show_timezone", sa.Boolean, nullable=False, server_default=sa.false()))

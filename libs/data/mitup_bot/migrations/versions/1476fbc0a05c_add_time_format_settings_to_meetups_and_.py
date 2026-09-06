"""add time format settings to meetups and settings

The columns are NOT NULL with server defaults, so the deployed image goes on inserting rows that
name none of them.

Revision ID: 1476fbc0a05c
Revises: 4d77028bd87b
Create Date: 2026-09-05 11:50:53.438620+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1476fbc0a05c"
down_revision: str | None = "4d77028bd87b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.add_column("meetups", sa.Column("show_timezone", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("meetups", sa.Column("clock_24h", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("meetups", sa.Column("date_format", sa.String(length=16), nullable=False, server_default="default"))
    op.add_column(
        "settings", sa.Column("default_show_timezone", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("settings", sa.Column("default_clock_24h", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column(
        "settings", sa.Column("default_date_format", sa.String(length=16), nullable=False, server_default="default")
    )


def downgrade():
    op.drop_column("meetups", "show_timezone")
    op.drop_column("meetups", "clock_24h")
    op.drop_column("meetups", "date_format")
    op.drop_column("settings", "default_show_timezone")
    op.drop_column("settings", "default_clock_24h")
    op.drop_column("settings", "default_date_format")

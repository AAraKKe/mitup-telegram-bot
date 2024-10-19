"""Add settings to meetings

Revision ID: c007da3db5c9
Revises: 3159492a167e
Create Date: 2024-10-19 20:24:48.500985+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c007da3db5c9"
down_revision: str | None = "3159492a167e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meetups", sa.Column("waiting_list", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("meetups", sa.Column("public", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("meetups", sa.Column("allow_invitation", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("meetups", sa.Column("incognito", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("meetups", sa.Column("show_timezone", sa.Boolean, nullable=False, server_default=sa.true()))

    # # Update existing values
    # op.execute("UPDATE meetups SET waiting_list = FALSE")
    # op.execute("UPDATE meetups SET public = FALSE")
    # op.execute("UPDATE meetups SET allow_invitation = FALSE")
    # op.execute("UPDATE meetups SET incognito = FALSE")
    # op.execute("UPDATE meetups SET show_timezone = TRUE")

    # # Make column not nullable
    # op.alter_column("meetups", "waiting_list", nullable=False)
    # op.alter_column("meetups", "public", nullable=False)
    # op.alter_column("meetups", "allow_invitation", nullable=False)
    # op.alter_column("meetups", "incognito", nullable=False)
    # op.alter_column("meetups", "show_timezone", nullable=False)


def downgrade() -> None:
    op.drop_column("meetups", "waiting_list")
    op.drop_column("meetups", "public")
    op.drop_column("meetups", "allow_invitation")
    op.drop_column("meetups", "incognito")
    op.drop_column("meetups", "show_timezone")

"""add deletion notice settings

The two per-user toggles for the deletion warning and the deletion notice, NOT NULL with a server
default of true so every existing row keeps both messages.

Revision ID: 55016f2b2d4e
Revises: d9b98605ac74
Create Date: 2026-09-08 10:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "55016f2b2d4e"
down_revision: str | None = "d9b98605ac74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.add_column("settings", sa.Column("deletion_warning", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("settings", sa.Column("deletion_notice", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column("settings", "deletion_notice")
    op.drop_column("settings", "deletion_warning")

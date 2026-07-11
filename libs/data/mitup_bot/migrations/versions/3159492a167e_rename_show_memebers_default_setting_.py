"""Rename show memebers default setting column

Revision ID: 3159492a167e
Revises: 640f660fe984
Create Date: 2024-10-19 12:49:13.149346+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3159492a167e"
down_revision: str | None = "640f660fe984"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.alter_column(
        table_name="settings",
        column_name="default_show_members",
        new_column_name="default_incognito",
    )


def downgrade():
    op.alter_column(
        table_name="settings",
        column_name="default_incognito",
        new_column_name="default_show_members",
    )

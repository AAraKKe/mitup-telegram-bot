"""Modify timestamp from create and update

Revision ID: 5731b33b7fc0
Revises: 55e82e53883c
Create Date: 2023-11-04 18:45:16.090195+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5731b33b7fc0"
down_revision: str | None = "55e82e53883c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.alter_column(
        table_name="users",
        column_name="created_time",
        server_default=sa.func.now(),
    )

    op.alter_column(
        table_name="users",
        column_name="updated_time",
        server_default=sa.func.now(),
    )


def downgrade():
    op.alter_column(
        table_name="users",
        column_name="created_time",
        server_default=None,
    )

    op.alter_column(
        table_name="users",
        column_name="updated_time",
        server_default=None,
    )

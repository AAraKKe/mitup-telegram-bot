"""Change nullable valor in create and update in user table

Revision ID: 741e3b4adacf
Revises: 6ab6bcf55eb7
Create Date: 2024-01-03 20:49:46.809573+00:00

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "741e3b4adacf"
down_revision: str | None = "6ab6bcf55eb7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        table_name="users",
        column_name="created_time",
        nullable=True,
    )
    op.alter_column(table_name="users", column_name="updated_time", nullable=True)


def downgrade() -> None:
    op.alter_column(
        table_name="users",
        column_name="created_time",
        nullable=False,
    )
    op.alter_column(table_name="users", column_name="updated_time", nullable=False)

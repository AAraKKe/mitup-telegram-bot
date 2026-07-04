"""Rename extension period to timeout

Revision ID: 316e7347bdd1
Revises: 44f4c5343e10
Create Date: 2024-12-08 14:36:44.006606+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "316e7347bdd1"
down_revision: str | None = "44f4c5343e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.alter_column("settings", "default_extension_period", new_column_name="timeout")


def downgrade():
    op.alter_column("settings", "timeout", new_column_name="default_extension_period")

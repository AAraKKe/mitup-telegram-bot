"""Add invited by id field to joined links

Revision ID: 965067fc3f1e
Revises: 207d4e7c7df9
Create Date: 2025-12-25 12:44:26.247594+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "965067fc3f1e"
down_revision: str | None = "207d4e7c7df9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("joined_users", sa.Column("invited_by_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_joined_users_invited_by_id", "joined_users", "users", ["invited_by_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_joined_users_invited_by_id", "joined_users", type_="foreignkey")
    op.drop_column("joined_users", "invited_by_id")

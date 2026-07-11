"""Add is_active to User

Revision ID: 90746097ea87
Revises: c007da3db5c9
Create Date: 2024-11-05 22:30:40.462379+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "90746097ea87"
down_revision: str | None = "c007da3db5c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.add_column("users", sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column("users", "is_active")

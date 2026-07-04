"""Add notification_sent to JoinedLinks

Revision ID: 44f4c5343e10
Revises: 90746097ea87
Create Date: 2024-11-09 14:24:19.750838+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "44f4c5343e10"
down_revision: str | None = "90746097ea87"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.add_column(
        "joined_users", sa.Column("notification_sent", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade():
    pass

"""Drop meeting_duration_enabled from settings

Revision ID: cd455e4de6dc
Revises: b217be05bdbf
Create Date: 2026-03-07 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cd455e4de6dc"
down_revision: str | None = "b217be05bdbf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("settings", "meeting_duration_enabled")


def downgrade() -> None:
    op.add_column(
        "settings",
        sa.Column("meeting_duration_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

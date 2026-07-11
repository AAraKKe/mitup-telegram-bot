"""drop revoked_time from premium_subscriptions

Revision ID: 384335105b9a
Revises: 5cc1a40ebdfc
Create Date: 2026-07-06 22:37:19.011954+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "384335105b9a"
down_revision: str | None = "5cc1a40ebdfc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.drop_column("premium_subscriptions", "revoked_time")


def downgrade():
    op.add_column(
        "premium_subscriptions",
        sa.Column("revoked_time", sa.DateTime, nullable=True),
    )

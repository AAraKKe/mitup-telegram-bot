"""add retry fields to broadcast deliveries

Give `broadcast_deliveries` the per-recipient retry substrate: `attempt_count` (how many times the
delivery has been claimed and sent; server_default "0" so the bulk audience insert, which omits it,
stays valid) and `next_attempt_time` (when a RETRY_PENDING row becomes claimable again; NULL for
every other status). The RETRY_PENDING status itself needs no DDL — the status column is a plain
VARCHAR(32) with native_enum=False.

Revision ID: ebbb61a5128b
Revises: 0d0d349b705a
Create Date: 2026-07-08 12:04:05.196101+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ebbb61a5128b"
down_revision: str | None = "0d0d349b705a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.add_column(
        "broadcast_deliveries",
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column("broadcast_deliveries", sa.Column("next_attempt_time", sa.DateTime, nullable=True))


def downgrade():
    op.drop_column("broadcast_deliveries", "next_attempt_time")
    op.drop_column("broadcast_deliveries", "attempt_count")

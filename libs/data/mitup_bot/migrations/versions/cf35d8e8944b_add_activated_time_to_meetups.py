"""add activated_time to meetups

Give `meetups` the timestamp the dateless deactivation window is measured from. A reactivation
restarts a meeting — it clears the dates and re-stamps `activated_time` — so the window has to run
from the last activation rather than from `created_time`, which stays the untouched provenance
timestamp.

Existing rows are backfilled with `created_time` (falling back to `now()` for the few rows a
pre-trigger insert left without one), which reproduces the window they were already being judged by.
The column then becomes NOT NULL with a `now()` server default: the currently deployed image inserts
meetups without this column, and the default is what keeps those inserts valid between the migration
and the image roll.

The backfill fires the `meetups` updated_time trigger on every row; no behaviour reads that column.

Revision ID: cf35d8e8944b
Revises: 975585c07344
Create Date: 2026-07-26 12:48:47.699697+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cf35d8e8944b"
down_revision: str | None = "3d6541d98bf4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_ACTIVATED_TIME_SQL = """
    UPDATE meetups
    SET activated_time = COALESCE(created_time, now())
    WHERE activated_time IS NULL;
"""


def upgrade():
    op.add_column("meetups", sa.Column("activated_time", sa.DateTime, nullable=True))
    op.execute(BACKFILL_ACTIVATED_TIME_SQL)
    op.alter_column("meetups", "activated_time", nullable=False, server_default=sa.text("now()"))


def downgrade():
    op.drop_column("meetups", "activated_time")

"""add users left_time

`users.left_time` records when a user entered LEFT status and is NULL in every other status. The
grace period in `apps/events/mitup_bot/events/user_cleanup.py` measures against it to decide when a
LEFT user held alive by nothing but a join link is demoted to JOINED_ONLY.

The column is nullable with no server default: every other status leaves it empty and the
application stamps it on the transition. LEFT rows are backfilled with `now()`, so their grace
period starts when this migration runs.

Revision ID: 3d6541d98bf4
Revises: 975585c07344
Create Date: 2026-07-26 12:51:11.478406+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3d6541d98bf4"
down_revision: str | None = "975585c07344"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAMP_LEFT_USERS_SQL = """
    UPDATE users
    SET left_time = now()
    WHERE status = 'left' AND left_time IS NULL;
"""


def upgrade():
    op.add_column("users", sa.Column("left_time", sa.DateTime, nullable=True))
    op.execute(STAMP_LEFT_USERS_SQL)


def downgrade():
    op.drop_column("users", "left_time")

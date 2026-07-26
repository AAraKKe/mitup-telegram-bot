"""stamp imported meetings expiration

Meetings imported from the old Heroku/Rails bot arrive with `expiration_time` set to `None`
regardless of their `active` state (see the Rails migration mapper). The cleanup
queries in `apps/events/mitup_bot/events/meetups_cleanup.py` only ever consider rows where
`expiration_time IS NOT NULL`, so every imported meeting that was already inactive is invisible
to both the deletion-warning and the permanent-deletion query and is retained forever.

Stamping `expiration_time` to now enrolls those rows in the normal lifecycle: the notification
query picks them up after the standard interval, same as any meeting that expired natively.

Revision ID: 975585c07344
Revises: 57115114b8f4
Create Date: 2026-07-26 00:00:00.000000+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "975585c07344"
down_revision: str | None = "57115114b8f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Backfills the imported-as-inactive meetings the Rails mapper left without an expiration stamp.
STAMP_IMPORTED_MEETINGS_SQL = """
    UPDATE meetups
    SET expiration_time = now()
    WHERE active = false AND expiration_time IS NULL;
"""


def upgrade():
    op.execute(STAMP_IMPORTED_MEETINGS_SQL)


def downgrade():
    # No-op: the stamped rows are indistinguishable from meetings that expired natively at the
    # same instant, so there is no way to single them back out to `NULL`.
    pass

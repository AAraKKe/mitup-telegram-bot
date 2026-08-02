"""add warned_time to meetups

Give `meetups` the timestamp the permanent deletion is held off from. The warning tells the owner
their meeting will be deleted in a fixed number of days, and the only record of it is the boolean
`expiration_notification_sent`; with the deletion timed from the deactivation stamp instead, a
warning the sweep issues late leaves the owner less than the promised lead — and past a delay of one
whole lead, none at all.

Existing warned rows are stamped with `now()` rather than a reconstructed date. Nothing in the
database says when their warning actually went out, and `now()` is the conservative answer: it grants
them a fresh full lead instead of deleting them on the strength of a guess. The cost is that a row
already at the end of its retention waits one more lead before it goes.

The column stays nullable, which is what keeps the currently deployed image's inserts valid between
this migration and the image roll.

The backfill fires the `meetups` updated_time trigger on every warned row; no behaviour reads that
column.

Revision ID: ed11fdab83a6
Revises: 082a246a66e7
Create Date: 2026-07-31 21:53:27.538026+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "ed11fdab83a6"
down_revision: str | None = "082a246a66e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Split out from the statement so the predicate can be checked against probe rows on its own: the
# UPDATE it belongs to matches on the flag alone, so running it takes a lock on every warned row.
WARNED_ROWS_PREDICATE = "expiration_notification_sent = true AND warned_time IS NULL"

BACKFILL_WARNED_TIME_SQL = f"""
    UPDATE meetups
    SET warned_time = now()
    WHERE {WARNED_ROWS_PREDICATE};
"""


def upgrade():
    op.add_column("meetups", sa.Column("warned_time", sa.DateTime, nullable=True))
    helpers.execute_bulk(revision, "backfill_warned_time", BACKFILL_WARNED_TIME_SQL)


def downgrade():
    op.drop_column("meetups", "warned_time")

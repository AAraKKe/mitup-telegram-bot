"""add acquisition_source and member_time to users

Give `users` the two facts a new-member report is built from: where a row came from, and when its
owner became a member.

`acquisition_source` is the arrival signal a row is stamped with when it is created — the raw
`/start` deep-link payload, or a sentinel naming the surface that created it. Telegram caps that
payload at 64 characters, which is where the column width comes from.

`member_time` is stamped when onboarding completes, not when the row appears: a row exists from the
first inline join or the first `/start`, and neither of those is the moment someone became a member.

Neither column is backfilled with a timestamp. Every row that already exists arrived — and became a
member, if it ever did — before anything recorded either, and NULL is the honest answer for them: a
default would claim a moment nobody observed. Both stay nullable, which is also what keeps the
currently deployed image's inserts valid between this migration and the image roll.

JOINED_ONLY rows are the one backfill. A row in that status was created by the inline Join button on
a shared meeting card, which is exactly the surface the sentinel names, so it can be attributed after
the fact. Placeholder invitees (`tg_user_id = -1`) are excluded — an owner typed those in, they
arrived from nowhere. The imprecision this accepts: a LEFT user the cleanup run demoted to
JOINED_ONLY is indistinguishable from an inline joiner afterwards, because the demotion clears
`left_time`, so a recently demoted ex-member is swept in and reported as a shared-card arrival.

Revision ID: 082a246a66e7
Revises: cf35d8e8944b
Create Date: 2026-07-28 22:27:10.693734+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "082a246a66e7"
down_revision: str | None = "cf35d8e8944b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Which rows the inline Join button is known to have created. Split out from the statement so the
# predicate can be checked against probe rows on its own: the UPDATE it belongs to matches on status
# alone, so running it takes a lock on every `users` row that qualifies.
SHARED_CARD_ROWS_PREDICATE = "status = 'joined_only' AND tg_user_id != -1 AND acquisition_source IS NULL"

# The sentinel is spelled out rather than imported: a migration has to keep applying to an old
# database exactly as it did the day it ran, whatever the application constant does afterwards.
BACKFILL_SHARED_CARD_SQL = f"""
    UPDATE users
    SET acquisition_source = 'card:shared'
    WHERE {SHARED_CARD_ROWS_PREDICATE};
"""


def upgrade():
    op.add_column("users", sa.Column("acquisition_source", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("member_time", sa.DateTime, nullable=True))
    helpers.execute_bulk(revision, "backfill_shared_card", BACKFILL_SHARED_CARD_SQL)


def downgrade():
    op.drop_column("users", "member_time")
    op.drop_column("users", "acquisition_source")

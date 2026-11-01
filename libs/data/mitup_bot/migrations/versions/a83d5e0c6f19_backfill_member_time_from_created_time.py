"""backfill member_time from created_time

A row with a member status and no `member_time` has no record of when its owner became a member, and
its creation is the closest known moment. That covers rows older than the column and newer rows that
reached MEMBER without finishing onboarding. `joined_only` rows stay NULL: most of them were never
members, and the ones that were cannot be told apart.

The downgrade relies on a real stamp always being later than the row's creation.

Revision ID: a83d5e0c6f19
Revises: 4f1c8a2de7b3
Create Date: 2026-11-01 12:30:00.000000+00:00

"""

from collections.abc import Sequence

from alembic import op  # noqa: F401

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "a83d5e0c6f19"
down_revision: str | None = "4f1c8a2de7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Split out from the statement so the predicate can be checked against probe rows on its own: the
# UPDATE takes a lock on every row that qualifies.
MEMBERS_WITHOUT_MEMBER_TIME_PREDICATE = "member_time IS NULL AND status IN ('member', 'left', 'deletion_requested')"

BACKFILL_MEMBER_TIME_SQL = f"""
    UPDATE users
    SET member_time = created_time
    WHERE {MEMBERS_WITHOUT_MEMBER_TIME_PREDICATE};
"""


def upgrade():
    helpers.execute_bulk(revision, "backfill_member_time", BACKFILL_MEMBER_TIME_SQL)


def downgrade():
    helpers.execute_bulk(
        revision, "clear_member_time", "UPDATE users SET member_time = NULL WHERE member_time = created_time"
    )

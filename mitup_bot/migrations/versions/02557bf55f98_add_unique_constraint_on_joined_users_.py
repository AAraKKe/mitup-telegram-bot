"""add unique constraint on joined_users user_id meetup_id

Enforce (user_id, meetup_id) uniqueness on joined_users at the database level so a
participant can hold at most one membership row per meeting. Duplicate prevention used
to live only in Python (Meetup.create_joined_link), which is a read-modify-write race
once two updates for the same user interleave.

The upgrade first removes any existing duplicates before adding the constraint: for each
(user_id, meetup_id) group it keeps a single row, preferring a non-waiting-list row over a
waiting-list one and, among equals, the oldest (earliest created_time, then lowest id).
Rows with a NULL user_id or meetup_id are left untouched — Postgres treats NULLs as
distinct, so they never collide with the constraint.

Revision ID: 02557bf55f98
Revises: 77c3abe2f1d0
Create Date: 2026-07-02 17:24:41.247605+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "02557bf55f98"
down_revision: str | None = "77c3abe2f1d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "uq_joined_users_user_id_meetup_id"

# Removes duplicate (user_id, meetup_id) memberships ahead of adding the constraint, keeping one row
# per group: a non-waiting-list row is preferred over a waiting-list one and, among equals, the oldest
# (earliest created_time, then lowest id). Rows with a NULL user_id or meetup_id are left untouched.
# Exposed at module scope so the migration's dedup test can assert against the exact statement.
DEDUPLICATE_JOINED_USERS_SQL = """
    DELETE FROM joined_users
    WHERE id IN (
        SELECT id
        FROM (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id, meetup_id
                    ORDER BY is_waiting_list ASC, created_time ASC, id ASC
                ) AS rn
            FROM joined_users
            WHERE user_id IS NOT NULL AND meetup_id IS NOT NULL
        ) ranked
        WHERE ranked.rn > 1
    );
"""


def upgrade():
    op.execute(DEDUPLICATE_JOINED_USERS_SQL)
    op.create_unique_constraint(CONSTRAINT_NAME, "joined_users", ["user_id", "meetup_id"])


def downgrade():
    op.drop_constraint(CONSTRAINT_NAME, "joined_users", type_="unique")

"""add tagged title and description to meetups

Revision ID: 6d469cde828e
Revises: fb38847b3ee7
Create Date: 2026-07-22 16:46:58.079210+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6d469cde828e"
down_revision: str | None = "fb38847b3ee7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Escape-only backfill: the tagged copy of an existing row is its plain text with the HTML
# special characters escaped (matching Python's html.escape), so tag-lookalike text a user
# typed keeps rendering literally once the tagged column is parsed for entities. `&` must be
# replaced first — later replacements introduce `&`-prefixed references that must not be
# re-escaped. A NULL description propagates through replace() and stays NULL.
ESCAPE_SQL_BY_COLUMN = {
    column: f"""replace(replace(replace(replace(replace({column},
        '&', '&amp;'),
        '<', '&lt;'),
        '>', '&gt;'),
        '"', '&quot;'),
        '''', '&#x27;')"""
    for column in ("title", "description")
}

BACKFILL_TAGGED_COLUMNS_SQL = f"""
    UPDATE meetups
    SET title_tagged = {ESCAPE_SQL_BY_COLUMN["title"]},
        description_tagged = {ESCAPE_SQL_BY_COLUMN["description"]}
    WHERE title_tagged IS NULL
"""


def upgrade():
    op.add_column("meetups", sa.Column("title_tagged", sa.Text(), nullable=True))
    op.add_column("meetups", sa.Column("description_tagged", sa.Text(), nullable=True))
    op.execute(BACKFILL_TAGGED_COLUMNS_SQL)


def downgrade():
    op.drop_column("meetups", "description_tagged")
    op.drop_column("meetups", "title_tagged")

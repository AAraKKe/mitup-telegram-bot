"""flip tagged titles and descriptions into the original columns

Revision ID: 52824dd9ee6b
Revises: 6d469cde828e
Create Date: 2026-07-22 17:13:17.522925+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "52824dd9ee6b"
down_revision: str | None = "6d469cde828e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copies the canonical tagged values into the original columns, which the code reads and
# writes in tagged form from this release on. Gating each copy on its own tagged column being
# non-NULL makes the statement idempotent and re-runnable (a description stays NULL where its
# tagged copy is NULL), so it can be re-invoked after a rollout if a row written plain by a
# still-running previous image is spotted.
COPY_TAGGED_SQL_BY_COLUMN = {
    column: f"UPDATE meetups SET {column} = {column}_tagged WHERE {column}_tagged IS NOT NULL"
    for column in ("title", "description")
}

# Reverses the flip in SQL: tags are removed first, then character references are decoded in
# the mirror order of the expand escape chain — `&amp;` last, so a decoded `&` is never
# re-interpreted as the start of another reference. Only the five references the escape side
# (Python's html.escape) emits are decoded. A literal `>` inside a quoted attribute value
# would end the tag match early; the dialect's only attribute is the numeric tg-emoji
# `emoji-id`, which cannot contain one.
STRIP_TAGS_SQL_BY_COLUMN = {
    column: f"""replace(replace(replace(replace(replace(
        regexp_replace({column}_tagged, '<[^>]*>', '', 'g'),
        '&#x27;', ''''),
        '&quot;', '"'),
        '&gt;', '>'),
        '&lt;', '<'),
        '&amp;', '&')"""
    for column in ("title", "description")
}

RESTORE_PLAIN_SQL_BY_COLUMN = {
    column: f"UPDATE meetups SET {column} = {STRIP_TAGS_SQL_BY_COLUMN[column]} WHERE {column}_tagged IS NOT NULL"
    for column in ("title", "description")
}


def upgrade():
    op.execute(COPY_TAGGED_SQL_BY_COLUMN["title"])
    op.execute(COPY_TAGGED_SQL_BY_COLUMN["description"])


def downgrade():
    op.execute(RESTORE_PLAIN_SQL_BY_COLUMN["title"])
    op.execute(RESTORE_PLAIN_SQL_BY_COLUMN["description"])

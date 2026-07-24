"""drop transitional tagged columns from meetups

Revision ID: 26719527cf8f
Revises: 52824dd9ee6b
Create Date: 2026-07-22 17:29:45.564182+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "26719527cf8f"
down_revision: str | None = "52824dd9ee6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.drop_column("meetups", "title_tagged")
    op.drop_column("meetups", "description_tagged")


def downgrade():
    op.add_column("meetups", sa.Column("title_tagged", sa.Text(), nullable=True))
    op.add_column("meetups", sa.Column("description_tagged", sa.Text(), nullable=True))
    # The original columns hold the canonical tagged form, so copying them back re-establishes
    # the invariant that every row carries a populated tagged copy — which the flip revision's
    # downgrade relies on (it only restores plain text WHERE the tagged copy IS NOT NULL).
    op.execute("UPDATE meetups SET title_tagged = title, description_tagged = description")

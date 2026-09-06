"""add meeting images

Adds `meeting_images`, the photos a meeting card shows as its banner, and `meetups.image_layout`,
which decides how several of them are arranged. The unique constraint on (meetup_id, position)
keeps every position distinct, and the rows are deleted with their meeting. `image_layout` is a
plain VARCHAR rather than a PostgreSQL enum type, like `date_format`.

Revision ID: d9b98605ac74
Revises: 1476fbc0a05c
Create Date: 2026-09-05 21:00:10.580588+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9b98605ac74"
down_revision: str | None = "1476fbc0a05c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEETING_IMAGES_UNIQUE_CONSTRAINT = "uq_meeting_images_meetup_id_position"
MEETING_IMAGES_MEETUP_INDEX = "ix_meeting_images_meetup_id"


def upgrade():
    op.create_table(
        "meeting_images",
        sa.Column("id", sa.Integer, nullable=False, primary_key=True),
        sa.Column("meetup_id", sa.BigInteger, sa.ForeignKey("meetups.id", ondelete="CASCADE"), nullable=True),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("file_id", sa.String, nullable=False),
        sa.Column("file_unique_id", sa.String, nullable=False),
        sa.UniqueConstraint("meetup_id", "position", name=MEETING_IMAGES_UNIQUE_CONSTRAINT),
    )
    op.create_index(MEETING_IMAGES_MEETUP_INDEX, "meeting_images", ["meetup_id"])
    op.add_column("meetups", sa.Column("image_layout", sa.String(length=16), nullable=False, server_default="collage"))


def downgrade():
    op.drop_column("meetups", "image_layout")
    op.drop_index(MEETING_IMAGES_MEETUP_INDEX, table_name="meeting_images")
    op.drop_table("meeting_images")
